"""Evidence binding: the anti-hallucination gate.

The guarantee under test is narrow and absolute: **a finding reaches the report
only if its snippet is found in the file at (or very near) the cited lines, and
the snippet in the report is the one read from disk.**

Each test here corresponds to a way a model gets it wrong in practice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codereview.models.findings import CandidateFinding, Evidence
from codereview.verification.evidence import bind_evidence, bind_many

# Contiguous lines 27-29 of buggy_repo/security_issues.py:
#   27:     assert request.user.is_admin
#   28:     user.delete()
#   29:     return True
CONTIGUOUS = "    assert request.user.is_admin\n    user.delete()\n    return True"

TARGET = "security_issues.py"


def make(evidence: Evidence, **overrides) -> CandidateFinding:
    payload = {
        "category": "bug",
        "severity": "high",
        "title": "test finding",
        "rationale": "because",
        "evidence": evidence,
    }
    payload.update(overrides)
    return CandidateFinding(**payload)


def test_real_snippet_passes_and_is_rebound_from_disk(buggy_repo: Path) -> None:
    finding = make(Evidence(path=TARGET, line_start=27, line_end=29, snippet=CONTIGUOUS))

    result = bind_evidence(buggy_repo, finding)

    assert result.verdict == "pending"
    assert result.rejected_by is None
    assert result.snippet_sha256
    # The reported snippet is the real one, byte for byte.
    assert result.evidence is not None
    assert result.evidence.snippet == CONTIGUOUS


def test_snippet_comes_from_disk_not_from_the_model(buggy_repo: Path) -> None:
    """The exact mechanism: the model's text is discarded, the file's is used."""
    finding = make(
        Evidence(
            path=TARGET,
            line_start=26,
            line_end=28,
            snippet="whatever the model felt like writing",
        )
    )

    result = bind_evidence(buggy_repo, finding)

    # A wholly invented snippet cannot be matched, so nothing is reported.
    assert result.verdict == "rejected"
    assert result.evidence is None


def test_fabricated_defect_is_rejected(buggy_repo: Path) -> None:
    """The classic hallucination: confident, plausible, not in the file."""
    finding = make(
        Evidence(
            path=TARGET,
            line_start=30,
            line_end=31,
            snippet='cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")',
        )
    )

    result = bind_evidence(buggy_repo, finding)

    assert result.verdict == "rejected"
    assert result.rejected_by == "evidence"
    assert "EVIDENCE_MISMATCH" in result.verdict_reason


@pytest.mark.parametrize("offset", [-5, -3, -1, 1, 3, 5])
def test_tolerates_line_number_drift(buggy_repo: Path, offset: int) -> None:
    """Models cite the right code with slightly wrong line numbers.

    Rejecting these would throw away true positives over an off-by-one, so the
    search widens by a few lines before giving up.
    """
    finding = make(
        Evidence(path=TARGET, line_start=27 + offset, line_end=29 + offset, snippet=CONTIGUOUS)
    )

    result = bind_evidence(buggy_repo, finding)

    assert result.verdict == "pending", f"offset {offset} should still match"


def test_drift_beyond_the_pad_is_rejected(buggy_repo: Path) -> None:
    finding = make(Evidence(path=TARGET, line_start=1, line_end=3, snippet=CONTIGUOUS))

    result = bind_evidence(buggy_repo, finding)

    assert result.verdict == "rejected"


def test_snippet_with_line_number_prefixes_still_matches(buggy_repo: Path) -> None:
    """read_file returns `NNNN: code`; models often copy the prefix too."""
    finding = make(
        Evidence(
            path=TARGET,
            line_start=26,
            line_end=28,
            snippet=(
                "27:     assert request.user.is_admin\n"
                "28:     user.delete()\n"
                "29:     return True"
            ),
        )
    )

    result = bind_evidence(buggy_repo, finding)

    assert result.verdict == "pending"


def test_snippet_with_different_indentation_still_matches(buggy_repo: Path) -> None:
    finding = make(
        Evidence(
            path=TARGET,
            line_start=26,
            line_end=28,
            snippet="assert request.user.is_admin\nuser.delete()\nreturn True",
        )
    )

    assert bind_evidence(buggy_repo, finding).verdict == "pending"


def test_out_of_range_is_rejected(buggy_repo: Path) -> None:
    finding = make(Evidence(path=TARGET, line_start=9000, line_end=9010, snippet="x = 1"))

    result = bind_evidence(buggy_repo, finding)

    assert result.verdict == "rejected"
    assert "OUT_OF_RANGE" in result.verdict_reason


def test_inverted_range_is_rejected(buggy_repo: Path) -> None:
    finding = make(Evidence(path=TARGET, line_start=29, line_end=27, snippet=CONTIGUOUS))

    assert bind_evidence(buggy_repo, finding).verdict == "rejected"


def test_path_escape_is_rejected(buggy_repo: Path) -> None:
    finding = make(
        Evidence(
            path="../../../Windows/System32/drivers/etc/hosts",
            line_start=1,
            line_end=1,
            snippet="127.0.0.1 localhost",
        )
    )

    result = bind_evidence(buggy_repo, finding)

    assert result.verdict == "rejected"
    assert "SANDBOX_VIOLATION" in result.verdict_reason


def test_missing_file_is_rejected(buggy_repo: Path) -> None:
    finding = make(Evidence(path="ghost.py", line_start=1, line_end=1, snippet="x"))

    result = bind_evidence(buggy_repo, finding)

    assert result.verdict == "rejected"
    assert "UNREADABLE" in result.verdict_reason


def test_empty_snippet_accepts_the_range(buggy_repo: Path) -> None:
    """No snippet is a formatting failure, not a fabrication.

    The range still points at real lines, and the verifier judges the claim on
    its merits -- rejecting here would drop findings for a cosmetic reason.
    """
    finding = make(Evidence(path=TARGET, line_start=27, line_end=29, snippet=""))

    result = bind_evidence(buggy_repo, finding)

    assert result.verdict == "pending"
    assert result.evidence is not None
    assert "assert request.user.is_admin" in result.evidence.snippet


def test_ruff_findings_are_confirmed_without_matching(buggy_repo: Path) -> None:
    """Tool output is ground truth; there is no model claim to check."""
    finding = make(
        Evidence(path=TARGET, line_start=8, line_end=8),
        source="ruff",
        rule_id="F401",
    )

    result = bind_evidence(buggy_repo, finding)

    assert result.verdict == "confirmed"
    assert result.rule_id == "F401"
    # The snippet is still filled in from disk, so the report shows real code.
    assert result.evidence is not None
    assert "json" in result.evidence.snippet


def test_bind_many_tolerates_mixed_batch(buggy_repo: Path) -> None:
    findings = [
        make(Evidence(path=TARGET, line_start=27, line_end=29, snippet=CONTIGUOUS)),
        make(Evidence(path="ghost.py", line_start=1, line_end=1, snippet="x")),
        make(Evidence(path=TARGET, line_start=1, line_end=1, snippet="nope")),
    ]

    results = bind_many(buggy_repo, findings)

    assert len(results) == 3
    assert sum(1 for r in results if r.verdict == "pending") == 1
    assert sum(1 for r in results if r.verdict == "rejected") == 2


def test_clean_code_produces_no_surviving_findings(clean_repo: Path) -> None:
    """The headline behaviour: nothing real to find, nothing reported.

    A model that invents a defect in clean code is caught here, and the count of
    rejections is what the report displays as proof the gate works.
    """
    invented = [
        make(
            Evidence(
                path="arithmetic.py",
                line_start=line,
                line_end=line + 2,
                snippet='cursor.execute(f"DELETE FROM users WHERE id = {uid}")',
            )
        )
        for line in (5, 20, 35)
    ]

    results = bind_many(clean_repo, invented)

    assert all(r.verdict == "rejected" for r in results)
    assert sum(1 for r in results if r.rejected_by == "evidence") == 3
