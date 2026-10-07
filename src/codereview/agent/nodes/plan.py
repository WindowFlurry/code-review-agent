"""Planning: decide what to review, and in what order.

Plan-and-execute, in miniature. The planner sees a compact digest of the
repository -- paths, sizes, lint counts -- and returns a prioritised task list.
It never sees file *contents*, which keeps the planning call cheap even on a
large repository, and it cannot invent files because the digest is built from
the deterministic inventory.

The node also folds in the deterministic findings (ruff, AST). Those are free
and trustworthy, so they are added regardless of what the planner decides; the
planner only controls where the *LLM's* attention goes.
"""

from __future__ import annotations

import logging

from langgraph.types import Send

from ...config import get_settings
from ...llm.factory import build_chat_model
from ...models.plan import FileTask, ReviewPlan
from ...tools.ast_tools import to_candidates as ast_to_candidates
from ...tools.lint_tools import to_candidates as lint_to_candidates
from ...verification.evidence import bind_many
from ..state import ReviewState

logger = logging.getLogger(__name__)

PLANNER_PROMPT = """\
You are planning a code review. You are given an inventory of a repository: \
file paths, sizes, line counts, complexity, and how many lint findings each file \
already has.

Decide which files to send to a reviewer, and in what order. Return a task list \
ordered highest-priority first.

Prioritise:

1. Files with lint findings -- something is already wrong there.
2. Files with high cyclomatic complexity or deep nesting, especially long ones.
   Complexity is where bugs live.
3. Larger files over tiny ones, since a 5-line file has little to find.
4. Application code over tests and fixtures.

Deprioritise or omit files that are trivial, mostly data, or already clean.

You may split a very long file into line ranges so it fits in one reviewer's
context, using `line_start` and `line_end`.

Only use paths that appear in the inventory. Set `priority` from 0 (look first)
to 100 (look last). Keep `rationale` to two or three sentences, written in
Chinese (简体中文). File paths stay as they are.
"""


def _build_digest(state: ReviewState) -> str:
    """Compact, deterministic summary of the repository for the planner."""
    files = [f for f in state.get("files", []) if f.reviewable]
    lint_by_file = state.get("lint_by_file", {})
    ast_by_file = state.get("ast_by_file", {})

    lines = ["path | lines | complexity | lint_findings | notes"]
    for meta in files:
        summary = ast_by_file.get(meta.path)
        complexity = getattr(summary, "max_complexity", 0) if summary else 0
        lint_count = len(lint_by_file.get(meta.path, []))

        notes: list[str] = []
        if summary is not None and not getattr(summary, "ok", True):
            notes.append("DOES NOT PARSE")
        if meta.truncated:
            notes.append("truncated")
        if meta.line_count > 450:
            notes.append("long: consider splitting into line ranges")

        lines.append(
            f"{meta.path} | {meta.line_count} | {complexity} | {lint_count} | {'; '.join(notes)}"
        )

    return "\n".join(lines)


def plan_node(state: ReviewState) -> dict:
    """Produce the review plan and fold in the deterministic findings."""
    settings = get_settings()
    root = state["repo_root"]
    files = [f for f in state.get("files", []) if f.reviewable]

    # --- deterministic findings, always included ---------------------------
    lint_candidates = []
    for findings in state.get("lint_by_file", {}).values():
        lint_candidates.extend(lint_to_candidates(findings))

    ast_candidates = []
    for summary in state.get("ast_by_file", {}).values():
        ast_candidates.extend(ast_to_candidates(summary))

    deterministic = bind_many(root, lint_candidates + ast_candidates)
    logger.info(
        "plan: %d deterministic finding(s) (%d lint, %d ast)",
        len(deterministic),
        len(lint_candidates),
        len(ast_candidates),
    )

    # --- LLM plan -----------------------------------------------------------
    if state.get("offline"):
        # Offline mode is an explicit choice, so skip the model entirely rather
        # than attempting a call that is known to fail. Deterministic findings
        # still reach the report; nothing else can.
        logger.info("plan: offline mode, skipping the LLM planner")
        return {
            "plan": ReviewPlan(tasks=[], rationale="offline: deterministic analysis only"),
            "tasks": [],
            "candidates": deterministic,
            "errors": [],
        }

    plan, errors = _ask_planner(state, settings, files)
    tasks = _tasks_from_plan(plan, files)

    return {
        "plan": plan,
        "tasks": tasks,
        "candidates": deterministic,
        "errors": errors,
    }


def _ask_planner(state: ReviewState, settings, files) -> tuple[ReviewPlan, list[str]]:
    """Ask the model for a plan, falling back to every file on any failure.

    The fallback matters: a planner outage should degrade to "review everything"
    rather than "review nothing". A review that silently covers no files is far
    worse than one that costs more than it needed to.
    """
    digest = _build_digest(state)

    try:
        model = build_chat_model("planner", settings)
        structured = model.with_structured_output(ReviewPlan)
        result = structured.invoke(
            [
                {"role": "system", "content": PLANNER_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Repository inventory:\n\n{digest}\n\n"
                        f"Produce a review plan. There are {len(files)} files."
                    ),
                },
            ]
        )
        if isinstance(result, ReviewPlan) and result.tasks:
            return result, []
        logger.warning("planner returned no tasks; falling back to all files")
        return _fallback_plan(files), ["planner returned no usable tasks"]
    except Exception as exc:  # noqa: BLE001 - planning must never abort a review
        logger.warning("planner failed (%s); falling back to all files", exc)
        return _fallback_plan(files), [f"planner failed, reviewed all files: {exc}"]


def _fallback_plan(files) -> ReviewPlan:
    return ReviewPlan(
        tasks=[
            FileTask(path=meta.path, priority=50, reason="fallback: review all files")
            for meta in files
        ],
        rationale="Planner unavailable; every reviewable file scheduled.",
    )


def _tasks_from_plan(plan: ReviewPlan, files) -> list[FileTask]:
    """Validate the planner's output against the real inventory.

    A model can hallucinate a path or cite a line range past the end of a file.
    Rather than trusting it, every task is checked against the inventory and
    dropped if it does not correspond to a real file.
    """
    known = {meta.path: meta for meta in files}
    tasks: list[FileTask] = []
    dropped = 0

    for task in plan.tasks:
        meta = known.get(task.path)
        if meta is None:
            dropped += 1
            continue

        # Clamp the window to the real file length.
        start = max(1, task.line_start)
        end = min(task.line_end or meta.line_count, meta.line_count)
        if start > end:
            dropped += 1
            continue

        tasks.append(task.model_copy(update={"line_start": start, "line_end": end}))

    if dropped:
        logger.warning("planner proposed %d task(s) for unknown or invalid paths", dropped)

    if not tasks:
        return _fallback_plan(files).tasks

    tasks.sort(key=lambda t: t.priority)
    return tasks


def fanout(state: ReviewState):
    """Map step: dispatch one reviewer invocation per planned task.

    Returns ``Send`` objects so LangGraph runs them concurrently. Each carries a
    *slice* of state rather than the whole thing -- passing the full state to N
    parallel branches would copy the entire inventory N times.

    With no tasks this returns the string ``"verify"`` instead of an empty list.
    An empty list would be interpreted as "no destination", ending the graph
    before the report is written -- so a repository whose plan came back empty
    would silently produce no output at all. Tool-sourced findings still need to
    reach the report, so the run must continue.
    """
    tasks = state.get("tasks", [])
    if not tasks:
        logger.info("fanout: no tasks to dispatch; continuing to verification")
        return "verify"

    if state.get("offline"):
        # A task list can only be non-empty here if something else produced it;
        # in offline mode the planner never does. Guard anyway so an offline run
        # can never silently start making model calls.
        logger.info("fanout: offline mode, skipping the reviewer")
        return "verify"

    logger.info("fanout: dispatching %d review task(s)", len(tasks))

    return [
        Send(
            "review_file",
            {
                "repo_root": state["repo_root"],
                "run_id": state["run_id"],
                "task": task,
                "lint_by_file": state.get("lint_by_file", {}),
                "ast_by_file": state.get("ast_by_file", {}),
                "only_files": state.get("only_files"),
            },
        )
        for task in tasks
    ]
