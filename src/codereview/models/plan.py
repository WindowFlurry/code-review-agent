"""Repository inventory and the review plan the LLM produces from it."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SkipReason = Literal[
    "binary",
    "too_large",
    "not_python",
    "unreadable",
    "empty",
    "vendored",
    "generated",
]


class FileMeta(BaseModel):
    """One file discovered during ingestion.

    ``FileMeta`` is produced deterministically; the model never invents entries.
    """

    path: str = Field(description="Repo-relative POSIX path.")
    size_bytes: int = 0
    line_count: int = 0
    language: str = "unknown"
    sha256: str = ""
    encoding: str = "utf-8"
    truncated: bool = False
    skipped: bool = False
    skip_reason: SkipReason | None = None

    @property
    def reviewable(self) -> bool:
        return not self.skipped


class FileTask(BaseModel):
    """A unit of review work handed to one reviewer sub-agent invocation.

    One file may produce several tasks when it is too large for a single prompt;
    each task carries the line window it covers.
    """

    path: str
    line_start: int = 1
    line_end: int | None = Field(
        default=None, description="Inclusive last line; None means 'to end of file'."
    )
    priority: int = Field(
        default=50, ge=0, le=100, description="0 = look first. Drives ordering under concurrency caps."
    )
    reason: str = Field(default="", description="Why the planner chose this file (or window).")


class ReviewPlan(BaseModel):
    """Structured-output target for the planning node."""

    tasks: list[FileTask] = Field(
        default_factory=list,
        description="Files or line windows to review, highest priority first.",
    )
    rationale: str = Field(
        default="", description="One short paragraph explaining the selection and ordering."
    )
    skipped: list[str] = Field(
        default_factory=list, description="Paths deliberately left out, with the reason in `rationale`."
    )
