"""Graph state.

Parallel branches merge into the state through reducers. Getting this wrong is
subtle and destructive: without ``operator.add``, whichever branch finishes last
silently *overwrites* the others' findings, and the review quietly loses work
with no error anywhere.

Anything that only one node writes (the plan, the report) is left unreduced --
those are last-write-wins by design, and adding a reducer would turn a bug into
a list.
"""

from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel

from ..models.findings import VerifiedFinding
from ..models.lint import AstSummary, LintFinding
from ..models.plan import FileMeta, FileTask, ReviewPlan
from ..models.report import ReviewReport


class Usage(BaseModel):
    """Token and call accounting for one node invocation.

    Accumulated as a list and summed at report time, rather than incremented in
    place: a shared mutable counter updated from parallel branches loses counts
    to races, and token accounting that silently under-reports is worse than none.
    """

    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_miss_tokens: int = 0
    output_tokens: int = 0
    malformed_responses: int = 0
    failed_files: int = 0


class ReviewState(TypedDict, total=False):
    """State threaded through the review graph."""

    # --- immutable inputs ---
    repo_root: str
    run_id: str
    started_at: datetime
    #: Deterministic-only mode. Set when the caller explicitly asked for it, so
    #: the LLM stages are skipped rather than attempted and failed.
    offline: bool
    #: When reviewing a single file, the repo-relative path of just that file.
    #: ``repo_root`` is then the file's *parent*, so path resolution still works.
    only_files: tuple[str, ...] | None

    # --- deterministic prefetch, written once by ingest/static_scan ---
    files: list[FileMeta]
    lint_by_file: dict[str, list[LintFinding]]
    ast_by_file: dict[str, AstSummary]

    # --- plan ---
    plan: ReviewPlan | None
    tasks: list[FileTask]

    # --- per-file work, merged from parallel branches ---
    #: Reducer note: without operator.add, parallel reviewers overwrite each other.
    #: Written ONLY by the fan-out branches (and the planner's deterministic pass).
    #: A node that wants to replace this list must write `verified` instead --
    #: with a reducer in place, returning a value here *appends* to it.
    candidates: Annotated[list[VerifiedFinding], operator.add]
    usage: Annotated[list[Usage], operator.add]
    errors: Annotated[list[str], operator.add]

    #: Deliberately NOT reduced: the verify node replaces the candidate list
    #: wholesale, and last-write-wins is the behaviour we want. Using
    #: `candidates` for this would duplicate every finding.
    verified: list[VerifiedFinding]

    # --- terminal ---
    summary: str
    abort_reason: str | None
    report: ReviewReport | None

    #: Conversation memory. Not used by the batch review itself, but present so
    #: a checkpointer can persist a thread for follow-up questions.
    messages: Annotated[list[AnyMessage], add_messages]


def empty_state(
    repo_root: str,
    run_id: str,
    *,
    offline: bool = False,
    only_files: tuple[str, ...] | None = None,
) -> ReviewState:
    return ReviewState(
        repo_root=repo_root,
        run_id=run_id,
        started_at=datetime.now(),
        offline=offline,
        only_files=only_files,
        files=[],
        lint_by_file={},
        ast_by_file={},
        plan=None,
        tasks=[],
        candidates=[],
        verified=[],
        usage=[],
        errors=[],
        summary="",
        abort_reason=None,
        report=None,
        messages=[],
    )


def total_usage(usages: list[Usage]) -> Usage:
    """Sum the per-node usage records into one total."""
    total = Usage()
    for item in usages:
        total.llm_calls += item.llm_calls
        total.tool_calls += item.tool_calls
        total.input_tokens += item.input_tokens
        total.cache_read_tokens += item.cache_read_tokens
        total.cache_miss_tokens += item.cache_miss_tokens
        total.output_tokens += item.output_tokens
        total.malformed_responses += item.malformed_responses
        total.failed_files += item.failed_files
    return total
