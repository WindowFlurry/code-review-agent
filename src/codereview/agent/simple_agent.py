"""The single-agent baseline: one ReAct loop over the whole repository.

Run with ``codereview review --mode single``.

This exists for two reasons. It satisfies the assignment's "basic agent loop:
input, reasoning, tool call, output" in the most literal way, and -- more
usefully -- it is the control in the architecture comparison. Same tools, same
model, same evidence binding and deterministic prefetch; the only thing removed
is the layered structure: no per-file fan-out, no independent verifier.

That makes the difference measurable rather than asserted. In practice the
baseline is cheaper on a small repository and starts missing things on a large
one, because it has to hold the whole repository in one conversation and nothing
checks its work. The numbers belong in DESIGN.md.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from langchain.agents import create_agent
from rich.console import Console

from ..config import Settings, get_settings
from ..ingestion import walk_repo
from ..llm.factory import build_chat_model
from ..models.report import ReviewReport, RunStats
from ..tools.ast_tools import python_ast_summary
from ..tools.ast_tools import to_candidates as ast_to_candidates
from ..tools.lint_tools import run_lint
from ..tools.lint_tools import to_candidates as lint_to_candidates
from ..tools.registry import ToolContext, build_reviewer_tools
from ..verification.evidence import bind_many
from .subagents.reviewer import REVIEWER_PROMPT

logger = logging.getLogger(__name__)

BASELINE_PREAMBLE = """\

## Mode

You are running in single-agent mode: you are reviewing the entire repository in
one pass, with no per-file structure. Use your tools to explore it yourself --
list the files, read the ones that look worth reviewing, and run the linter.

Budget your effort. You will not be able to read everything closely, so prioritise
the files most likely to hold real defects.
"""


def run_simple_review(
    target: Path,
    *,
    run_id: str = "single",
    only_files: tuple[str, ...] | None = None,
    settings: Settings | None = None,
    console: Console | None = None,
) -> dict:
    """Review a repository with one agent and return a state-like dict.

    Returns the same shape the graph produces, so the runner and the report
    renderers work unchanged.

    Args:
        target: Directory to review. A single file is handled by passing its
            parent here with ``only_files`` set to the filename.
        only_files: Restrict the inventory to these repo-relative paths.
    """
    settings = settings or get_settings()
    console = console or Console()
    started = datetime.now()

    files = walk_repo(
        target,
        max_files=settings.max_files,
        max_file_bytes=settings.max_file_bytes,
        languages=("python",),
        only_files=only_files,
    )
    reviewable = [f for f in files if f.reviewable]

    if not reviewable:
        return _aborted_state(target, run_id, started, files)

    # Same deterministic prefetch as the graph, so the comparison isolates the
    # architecture rather than the availability of lint results.
    lint_candidates = []
    try:
        lint_candidates = lint_to_candidates(run_lint(target))
    except Exception as exc:  # noqa: BLE001
        logger.warning("ruff unavailable in single mode: %s", exc)

    ast_candidates = []
    for meta in reviewable:
        try:
            ast_candidates.extend(
                ast_to_candidates(python_ast_summary(target, meta.path))
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("ast failed for %s: %s", meta.path, exc)

    deterministic = bind_many(str(target), lint_candidates + ast_candidates)

    ctx = ToolContext(root=target, settings=settings, restrict_to=only_files or None)
    agent = create_agent(
        model=build_chat_model("reviewer", settings),
        tools=build_reviewer_tools(ctx),
        system_prompt=REVIEWER_PROMPT + BASELINE_PREAMBLE,
        response_format=_review_findings_schema(),
    )

    inventory = "\n".join(
        f"{meta.path} ({meta.line_count} lines)" for meta in reviewable
    )
    prompt = (
        f"Repository: {target}\n\n"
        f"Files to review:\n{inventory}\n\n"
        "Review this repository and report the defects you can prove."
    )

    console.print("[dim]single-agent baseline: one pass over the whole repository[/dim]")

    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": prompt}]},
            config={"configurable": {"thread_id": f"{run_id}:single"}},
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("single-agent review failed")
        return _aborted_state(target, run_id, started, files, reason=f"agent failed: {exc}")

    findings = _extract(result)
    bound = bind_many(str(target), findings, max_bytes=settings.max_file_bytes)

    verified = [*deterministic, *bound]
    report = _build_report(target, run_id, started, files, verified, result)

    return {"report": report, "verified": verified, "files": files, "usage": []}


def _review_findings_schema():
    """Structured-output strategy for the baseline.

    Must be ``ToolStrategy`` for the same reason as the reviewer: left to pick,
    the agent chooses provider-native structured output and DeepSeek answers
    with a 400.
    """
    from langchain.agents.structured_output import ToolStrategy

    from ..models.findings import ReviewFindings

    return ToolStrategy(ReviewFindings)


def _extract(result: dict) -> list:
    from ..models.findings import ReviewFindings

    structured = result.get("structured_response")
    if isinstance(structured, ReviewFindings):
        return list(structured.findings)
    if isinstance(structured, dict):
        try:
            return list(ReviewFindings.model_validate(structured).findings)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not parse baseline findings: %s", exc)
    return []


def _build_report(target, run_id, started, files, verified, result) -> ReviewReport:
    reportable = [f for f in verified if f.is_reportable]

    stats = RunStats(
        files_scanned=len(files),
        files_reviewed=len([f for f in files if f.reviewable]),
        files_skipped=len([f for f in files if f.skipped]),
        candidates=len(verified),
        confirmed=sum(1 for f in verified if f.verdict == "confirmed"),
        rejected_by_evidence=sum(1 for f in verified if f.rejected_by == "evidence"),
        llm_calls=sum(
            1 for m in result.get("messages", []) if getattr(m, "usage_metadata", None)
        ),
        started_at=started,
        finished_at=datetime.now(),
    )

    return ReviewReport(
        repo_root=str(target),
        run_id=run_id,
        findings=verified,
        stats=stats,
        summary=(
            f"Single-agent baseline: {len(reportable)} finding(s) across "
            f"{len({f.evidence.path for f in reportable if f.evidence})} file(s). "
            "No independent verification stage."
        ),
    )


def _aborted_state(target, run_id, started, files, reason: str | None = None) -> dict:
    reason = reason or "no reviewable Python files found"
    return {
        "report": ReviewReport(
            repo_root=str(target),
            run_id=run_id,
            findings=[],
            stats=RunStats(
                files_scanned=len(files),
                files_skipped=len([f for f in files if f.skipped]),
                started_at=started,
                finished_at=datetime.now(),
            ),
            aborted=True,
            abort_reason=reason,
        ),
        "verified": [],
        "files": files,
        "usage": [],
    }
