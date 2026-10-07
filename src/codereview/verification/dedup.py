"""Overlap-based de-duplication of findings.

Two reviewers looking at the same file -- or one reviewer looking at two
overlapping windows of a large file -- will happily report the same defect
twice. Dedup runs after aggregation so the report shows each defect once.

Matching is deliberately conservative: same file, overlapping or adjacent line
ranges, and the same category. Two genuinely different defects that happen to
sit three lines apart (``eval()`` and a bare ``except``) must both survive, so
category has to match.
"""

from __future__ import annotations

import logging

from ..models.findings import VerifiedFinding

logger = logging.getLogger(__name__)

#: Ranges within this many lines of each other are treated as overlapping.
#: Small, because a false merge silently loses a real defect.
_ADJACENCY = 2

#: Severity ranking used to pick the survivor when two findings merge.
_RANK = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}


def _ranges_overlap(a: VerifiedFinding, b: VerifiedFinding) -> bool:
    if a.evidence is None or b.evidence is None:
        return False
    if a.evidence.path != b.evidence.path:
        return False
    if a.category != b.category:
        return False
    gap = _ADJACENCY
    return not (
        a.evidence.line_end + gap < b.evidence.line_start
        or b.evidence.line_end + gap < a.evidence.line_start
    )


def _merge(primary: VerifiedFinding, duplicate: VerifiedFinding) -> VerifiedFinding:
    """Fold *duplicate* into *primary*, keeping the more alarming of the two.

    The higher severity wins and the higher confidence wins, independently. The
    rationale of the loser is preserved in ``verdict_reason`` so the reviewer's
    alternative explanation is not lost.
    """
    if _RANK.get(duplicate.severity, 0) > _RANK.get(primary.severity, 0):
        primary.severity = duplicate.severity

    if duplicate.confidence > primary.confidence:
        primary.confidence = duplicate.confidence

    if duplicate.verdict == "confirmed" and primary.verdict != "confirmed":
        primary.verdict = "confirmed"
        primary.verdict_reason = duplicate.verdict_reason

    if duplicate.rationale.strip() and duplicate.rationale not in primary.verdict_reason:
        note = f"also reported: {duplicate.rationale.strip()[:200]}"
        primary.verdict_reason = f"{primary.verdict_reason} | {note}".strip(" |")

    return primary


def deduplicate(findings: list[VerifiedFinding]) -> list[VerifiedFinding]:
    """Collapse overlapping findings, keeping the most severe of each cluster.

    Order is preserved for the survivors, so a caller that sorted by severity
    beforehand keeps its ordering.
    """
    kept: list[VerifiedFinding] = []
    merged_count = 0

    for finding in findings:
        merged_into: VerifiedFinding | None = None
        for existing in kept:
            if _ranges_overlap(existing, finding):
                merged_into = existing
                break

        if merged_into is None:
            kept.append(finding)
        else:
            _merge(merged_into, finding)
            merged_count += 1

    if merged_count:
        logger.debug("deduplicated %d overlapping finding(s)", merged_count)

    return kept
