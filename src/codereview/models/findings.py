"""The finding data model -- the contract between review, verification and report.

The split is deliberate and mirrors the pipeline stages:

``CandidateFinding``  what the reviewer model *claims*
``VerifiedFinding``   what survived verification, with evidence re-read from disk

Keeping them separate means a rejected finding is still representable, so the
report can show "7 candidates, 0 confirmed" instead of silently dropping them.
That negative result is the headline proof that the anti-hallucination gate works.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "high", "medium", "low", "info"]
FindingCategory = Literal[
    "bug",
    "security",
    "performance",
    "style",
    "maintainability",
    "test-gap",
]
Verdict = Literal["pending", "confirmed", "rejected", "uncertain"]

#: Ordered worst-first; used to sort the report.
SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}


class Evidence(BaseModel):
    """A pointer to real code, plus the code itself.

    The ``snippet`` field is the model's *claim* about the code. After
    verification it is overwritten with what is actually on disk -- the model has
    no control over the text that reaches the report.
    """

    path: str = Field(description="Repo-relative POSIX path, exactly as returned by list_files.")
    line_start: int = Field(ge=1, description="1-based first line of the cited range.")
    line_end: int = Field(ge=1, description="1-based last line, inclusive.")
    snippet: str = Field(
        default="",
        description=(
            "The cited lines copied verbatim from read_file output. "
            "Do not paraphrase, summarise, or reconstruct from memory."
        ),
    )


class CandidateFinding(BaseModel):
    """A defect claimed by the reviewer, before verification."""

    category: FindingCategory = Field(description="What kind of problem this is.")
    severity: Severity = Field(description="Impact if left unfixed.")
    title: str = Field(description="One short line naming the defect.")
    rationale: str = Field(
        description="Why this is a defect, referring explicitly to the evidence snippet."
    )
    evidence: Evidence = Field(description="Where the defect lives. Must be real code.")
    suggested_fix: str | None = Field(
        default=None,
        description="A short before/after diff or concrete change. Null if no fix is obvious.",
    )
    confidence: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Self-reported confidence, 0 to 1."
    )

    #: Populated for findings that came from a deterministic tool rather than the
    #: model (ruff, AST). Tool-sourced findings skip the evidence-binding gate
    #: because their line numbers come from the tool itself.
    source: Literal["llm", "ruff", "ast"] = "llm"
    rule_id: str | None = Field(default=None, description="Lint rule code, e.g. F401.")


class ReviewFindings(BaseModel):
    """Structured-output target for the reviewer sub-agent."""

    findings: list[CandidateFinding] = Field(
        default_factory=list,
        description="Every defect found in this file. Return an empty list if the code is clean.",
    )
    notes: str | None = Field(
        default=None,
        description="Anything worth flagging that is not a concrete defect (e.g. file too large to review fully).",
    )


class VerifierVerdict(BaseModel):
    """Structured-output target for the verification sub-agent."""

    verdict: Literal["confirmed", "rejected", "uncertain"] = Field(
        description="`rejected` unless the evidence snippet alone proves the defect."
    )
    reason: str = Field(description="One or two sentences justifying the verdict.")
    corrected_severity: Severity | None = Field(
        default=None, description="Set only if the original severity is clearly wrong."
    )


class VerifiedFinding(BaseModel):
    """A candidate after the verification stage.

    Rejected findings are kept, not discarded, so run statistics can report how
    many hallucinations the gate caught.
    """

    category: FindingCategory
    severity: Severity
    title: str
    rationale: str
    evidence: Evidence | None = None
    suggested_fix: str | None = None
    confidence: float = 0.5

    verdict: Verdict = "pending"
    verdict_reason: str = ""
    rejected_by: Literal["evidence", "verifier"] | None = None

    #: SHA-256 of the canonical snippet, so a report can be checked against the
    #: file it came from without re-reading it.
    snippet_sha256: str | None = None

    source: Literal["llm", "ruff", "ast"] = "llm"
    rule_id: str | None = None

    @property
    def is_reportable(self) -> bool:
        """True for findings worth showing the user.

        ``uncertain`` is reported rather than dropped -- the user can judge it,
        but the report labels it as unproven.
        """
        return self.verdict in ("confirmed", "uncertain")

    @property
    def sort_key(self) -> tuple[int, str, int]:
        return (SEVERITY_ORDER.get(self.severity, 9), self.evidence.path if self.evidence else "",
                self.evidence.line_start if self.evidence else 0)
