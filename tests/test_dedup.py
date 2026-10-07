"""Deduplication of overlapping findings.

The tension here is real: merging too eagerly silently deletes true positives,
while merging too little floods the report with the same defect three times.
The tests below pin down both edges.
"""

from __future__ import annotations

from codereview.models.findings import Evidence, VerifiedFinding
from codereview.verification.dedup import deduplicate


def make(
    path: str = "a.py",
    start: int = 10,
    end: int = 12,
    category: str = "bug",
    severity: str = "medium",
    title: str = "t",
    confidence: float = 0.5,
) -> VerifiedFinding:
    return VerifiedFinding(
        category=category,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        title=title,
        rationale="r",
        evidence=Evidence(path=path, line_start=start, line_end=end, snippet="x"),
        verdict="confirmed",
        confidence=confidence,
    )


def test_identical_findings_collapse_to_one() -> None:
    assert len(deduplicate([make(), make()])) == 1


def test_overlapping_ranges_in_same_file_collapse() -> None:
    assert len(deduplicate([make(start=10, end=15), make(start=13, end=18)])) == 1


def test_adjacent_ranges_collapse() -> None:
    assert len(deduplicate([make(start=10, end=12), make(start=14, end=16)])) == 1


def test_distant_ranges_stay_separate() -> None:
    assert len(deduplicate([make(start=10, end=12), make(start=40, end=42)])) == 2


def test_same_lines_different_category_stay_separate() -> None:
    """`eval()` and a bare `except` three lines apart are different defects."""
    findings = [make(category="security"), make(category="style")]

    assert len(deduplicate(findings)) == 2


def test_same_lines_different_file_stay_separate() -> None:
    assert len(deduplicate([make(path="a.py"), make(path="b.py")])) == 2


def test_merge_keeps_the_higher_severity() -> None:
    merged = deduplicate([make(severity="low"), make(severity="critical")])

    assert len(merged) == 1
    assert merged[0].severity == "critical"


def test_merge_keeps_the_higher_confidence() -> None:
    merged = deduplicate([make(confidence=0.3), make(confidence=0.9)])

    assert merged[0].confidence == 0.9


def test_merge_preserves_the_losing_rationale() -> None:
    """The second reviewer's explanation should not vanish silently."""
    first = make(title="first")
    second = make(title="second")
    second.rationale = "an entirely different explanation of the same defect"

    merged = deduplicate([first, second])

    assert "different explanation" in merged[0].verdict_reason


def test_findings_without_evidence_are_not_merged() -> None:
    """Nothing to compare against; dropping them here would hide rejections."""
    bare = make()
    bare.evidence = None

    assert len(deduplicate([bare, bare.model_copy()])) == 2


def test_empty_input() -> None:
    assert deduplicate([]) == []
