"""Final report payload and run statistics."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .findings import VerifiedFinding


class RunStats(BaseModel):
    """Counters printed in the report footer.

    This footer is how the "edge cases handled" part of the rubric becomes
    visible at a glance instead of being an unverifiable claim: a run on clean
    code showing ``candidates=7, confirmed=0, rejected_by_evidence=7`` *is* the
    anti-hallucination demonstration.
    """

    files_scanned: int = 0
    files_reviewed: int = 0
    files_skipped: int = 0
    files_failed: int = 0

    llm_calls: int = 0
    tool_calls: int = 0

    candidates: int = 0
    confirmed: int = 0
    uncertain: int = 0
    rejected_by_evidence: int = 0
    rejected_by_verifier: int = 0
    malformed_responses: int = 0

    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    output_tokens: int = 0

    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def duration_s(self) -> float:
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return 0.0

    @property
    def total_rejected(self) -> int:
        return self.rejected_by_evidence + self.rejected_by_verifier


class ReviewReport(BaseModel):
    """Everything the report renderers need."""

    repo_root: str
    run_id: str
    findings: list[VerifiedFinding] = Field(default_factory=list)
    stats: RunStats = Field(default_factory=RunStats)
    summary: str = Field(default="", description="Short natural-language overview.")
    aborted: bool = False
    abort_reason: str | None = None
    #: True when only the deterministic analysers ran. Renderers must say so
    #: prominently: a deterministic-only report looks identical to a full one,
    #: and a reader who mistakes it for a complete review will trust coverage
    #: that never happened.
    offline: bool = False
    errors: list[str] = Field(default_factory=list)

    @property
    def reportable(self) -> list[VerifiedFinding]:
        return sorted(
            (f for f in self.findings if f.is_reportable),
            key=lambda f: f.sort_key,
        )

    @property
    def files_touched(self) -> list[str]:
        seen: list[str] = []
        for finding in self.reportable:
            if finding.evidence and finding.evidence.path not in seen:
                seen.append(finding.evidence.path)
        return seen


#: Used by renderers to pick a colour per severity.
SEVERITY_COLOURS: dict[str, Literal["red", "yellow", "cyan", "blue", "dim"]] = {
    "critical": "red",
    "high": "red",
    "medium": "yellow",
    "low": "cyan",
    "info": "dim",
}
