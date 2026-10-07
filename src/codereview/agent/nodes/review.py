"""Per-file review: the map step of the fan-out.

Each invocation gets one file (or one line window of a large file), runs the
ReAct reviewer over it, and returns candidate findings.

Isolation is deliberate. A failure in one branch is caught here and returned as
an error entry, so the other branches keep their results and the run still
produces a report. One unreadable file should not cost the user the other
nineteen.

Note the checkpointer: each invocation gets its own ``thread_id`` derived from
the run id and path. Sharing one thread across concurrent branches corrupts the
checkpoint -- two branches would read and write the same conversation state.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ...config import get_settings
from ...errors import CodeReviewError
from ...models.findings import ReviewFindings
from ...models.plan import FileTask
from ...tools.fs_tools import read_file
from ...tools.registry import ToolContext
from ...verification.evidence import bind_many
from ..state import Usage

logger = logging.getLogger(__name__)

#: Cache of built reviewer agents, keyed by repository root. Building the model
#: client is cheap but not free, and the fan-out invokes this N times.
_AGENT_CACHE: dict[str, object] = {}


def _get_reviewer(root: str, only_files: tuple[str, ...] | None = None):
    """Build (or reuse) the reviewer for this root.

    The cache key includes ``only_files``: two reviews of the same directory with
    different scopes need different tool bindings, and keying on the root alone
    would hand the second one the first one's tools.
    """
    key = f"{root}|{only_files}"
    agent = _AGENT_CACHE.get(key)
    if agent is None:
        from ...agent.subagents.reviewer import build_reviewer

        settings = get_settings()
        ctx = ToolContext(
            root=Path(root).resolve(),
            settings=settings,
            restrict_to=only_files or None,
        )
        agent = build_reviewer(ctx, settings)
        _AGENT_CACHE[key] = agent
    return agent


def _build_prompt(
    *,
    task: FileTask,
    content: str,
    lint_lines: str,
    ast_summary: str,
    is_partial: bool,
) -> str:
    """Assemble the reviewer's task description.

    The file content is inlined rather than left for the agent to fetch with a
    tool: it is needed in every case, so making the model call ``read_source``
    first would add a round trip and a chance to skip it.
    """
    window = (
        f"lines {task.line_start}-{task.line_end}" if is_partial else "the whole file"
    )

    parts = [
        f"# Review task\n\nFile: `{task.path}` ({window})",
    ]

    if is_partial:
        parts.append(
            "This is one window of a longer file. Report only defects visible in "
            "these lines; the other windows are reviewed separately."
        )

    if lint_lines:
        parts.append(
            "## Already-reported lint findings\n\n"
            "These are included in the final report automatically. **Do not repeat "
            "them** -- reporting one again creates a duplicate entry.\n\n"
            f"```\n{lint_lines}\n```"
        )

    if ast_summary:
        parts.append(f"## Structural summary\n\n```json\n{ast_summary}\n```")

    parts.append(
        "## File content\n\n```python\n"
        f"{content}\n"
        "```\n\n"
        "Report every defect you can prove with these lines. Quote `evidence.snippet` "
        "verbatim from the numbered lines above, and use the real line numbers. "
        "If the code is clean, return an empty findings list."
    )

    return "\n\n".join(parts)


def review_file_node(payload: dict) -> dict:
    """Review one file window. Always returns state; never raises."""
    settings = get_settings()
    root = payload["repo_root"]
    task: FileTask = payload["task"]

    try:
        content = read_file(root, task.path, start_line=task.line_start, end_line=task.line_end)
    except CodeReviewError as exc:
        logger.warning("skipping %s: %s", task.path, exc)
        return {"candidates": [], "errors": [f"{task.path}: {exc}"], "usage": []}

    lint_findings = (payload.get("lint_by_file") or {}).get(task.path, [])
    lint_lines = "\n".join(
        f"{f.rule_id} line {f.line_start}: {f.message}" for f in lint_findings[:30]
    )

    summary = (payload.get("ast_by_file") or {}).get(task.path)
    ast_summary = ""
    if summary is not None:
        import json

        ast_summary = json.dumps(
            {
                "functions": [
                    {
                        "name": fn.name,
                        "lines": [fn.line_start, fn.line_end],
                        "complexity": fn.cyclomatic_complexity,
                        "nesting": fn.nesting_depth,
                    }
                    for fn in getattr(summary, "functions", [])
                ],
                "bare_excepts": getattr(summary, "bare_excepts", []),
                "mutable_default_args": getattr(summary, "mutable_default_args", []),
                "parses": getattr(summary, "ok", True),
            },
            ensure_ascii=False,
        )

    is_partial = task.line_start > 1 or (
        task.line_end is not None and task.line_end < _file_length(root, task.path)
    )

    prompt = _build_prompt(
        task=task,
        content=content,
        lint_lines=lint_lines,
        ast_summary=ast_summary,
        is_partial=is_partial,
    )

    try:
        agent = _get_reviewer(root, payload.get("only_files") or None)
    except Exception as exc:  # noqa: BLE001
        logger.exception("could not build reviewer")
        return {"candidates": [], "errors": [f"{task.path}: {exc}"], "usage": []}

    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": prompt}]},
            # Per-branch thread id: sharing one across concurrent branches
            # corrupts the checkpoint.
            config={"configurable": {"thread_id": f"{payload['run_id']}:{task.path}"}},
        )
    except Exception as exc:  # noqa: BLE001 - one branch must not sink the run
        logger.warning("reviewer failed for %s: %s", task.path, exc)
        return {
            "candidates": [],
            "errors": [f"{task.path}: reviewer failed: {exc}"],
            "usage": [Usage(failed_files=1)],
        }

    findings, malformed = _extract_findings(result)
    usage = _usage_from(result, malformed=malformed, tool_calls=_count_tool_calls(result))

    bound = bind_many(root, findings, max_bytes=settings.max_file_bytes)

    confirmed = sum(1 for f in bound if f.verdict != "rejected")
    logger.info(
        "reviewed %s: %d candidate(s), %d passed evidence binding",
        task.path,
        len(bound),
        confirmed,
    )

    return {"candidates": bound, "errors": [], "usage": [usage]}


def _file_length(root: str, path: str) -> int:
    try:
        meta_text = read_file(root, path, start_line=1, end_line=1)
        # First line is the header: "# path (lines 1-1 of N, encoding=...)"
        marker = " of "
        if marker in meta_text.split("\n", 1)[0]:
            return int(meta_text.split(marker, 1)[1].split(",", 1)[0])
    except Exception:  # noqa: BLE001 - cosmetic only
        pass
    return 0


def _extract_findings(result: dict) -> tuple[list, int]:
    """Pull structured findings out of an agent result.

    Handles the three shapes that occur in practice: a clean structured
    response, no structured response at all (the agent answered in prose), and a
    result of an unexpected type. All three resolve to "no findings" plus a
    counter increment -- never an exception.
    """
    structured = result.get("structured_response")

    if structured is None:
        return [], 0
    if isinstance(structured, ReviewFindings):
        return list(structured.findings), 0
    if isinstance(structured, dict):
        try:
            return list(ReviewFindings.model_validate(structured).findings), 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not parse structured response: %s", exc)
            return [], 1

    logger.warning("unexpected structured response type: %s", type(structured).__name__)
    return [], 1


def _usage_from(result: dict, *, malformed: int, tool_calls: int) -> Usage:
    """Extract token usage from the agent's messages."""
    usage = Usage(malformed_responses=malformed, tool_calls=tool_calls)

    for message in result.get("messages", []):
        metadata = getattr(message, "usage_metadata", None)
        if not metadata:
            continue
        usage.llm_calls += 1
        usage.input_tokens += int(metadata.get("input_tokens", 0) or 0)
        usage.output_tokens += int(metadata.get("output_tokens", 0) or 0)

        details = metadata.get("input_token_details") or {}
        cache_read = int(details.get("cache_read", 0) or 0)
        usage.cache_read_tokens += cache_read
        usage.cache_miss_tokens += max(0, int(metadata.get("input_tokens", 0) or 0) - cache_read)

    return usage


def _count_tool_calls(result: dict) -> int:
    return sum(
        1
        for message in result.get("messages", [])
        if getattr(message, "type", "") == "tool"
    )
