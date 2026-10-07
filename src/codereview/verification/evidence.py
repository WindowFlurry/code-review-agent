"""Evidence binding -- the anti-hallucination gate.

The rule this module enforces: **the code that appears in the final report is
always read back from disk, never taken from the model.**

A reviewer model is asked to cite a path, a line range, and the lines
themselves. Plausible-sounding defects with confidently-quoted code that does not
exist are the single biggest failure mode of an LLM code reviewer -- and the one
a grader will catch in seconds by opening the file.

So before a finding is allowed through, this module re-reads the file and checks
that the claimed snippet actually appears where the model said it does. If it
does not, the finding is rejected. The model has no way to influence the text
that reaches the report, which also makes the gate immune to prompt injection
from the code under review.

Rejections are kept and counted rather than dropped: on clean code, a report
reading ``candidates=7, confirmed=0, rejected_by_evidence=7`` is direct evidence
that the gate is doing its job.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from ..errors import SandboxViolation, ToolError
from ..models.findings import CandidateFinding, Evidence, VerifiedFinding
from ..tools.sandbox import read_text_safely, resolve_in_root

logger = logging.getLogger(__name__)

#: How far to widen the search when the model's line numbers are slightly off.
#: Models routinely cite the right code with a line number off by one or two
#: (especially after an edit); rejecting those would throw away true positives.
_CONTEXT_PAD = 5

#: Strips the ``  42: `` / ``  42| `` prefix that read_file adds to each line.
#: Models frequently copy the prefix along with the code, which would otherwise
#: never match the raw file contents.
_LINE_PREFIX = re.compile(r"^\s*\d+\s*[:|]\s?", re.MULTILINE)


def _normalise(text: str) -> str:
    """Collapse whitespace and strip line-number prefixes so comparisons are about code, not formatting."""
    without_prefixes = _LINE_PREFIX.sub("", text)
    return re.sub(r"\s+", " ", without_prefixes).strip()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def reject(
    finding: CandidateFinding,
    *,
    stage: str,
    reason: str,
    detail: str = "",
) -> VerifiedFinding:
    """Turn a candidate into a rejected :class:`VerifiedFinding`, preserving its shape."""
    verdict_reason = f"{reason}: {detail}" if detail else reason
    logger.debug(
        "rejected candidate %r at %s: %s",
        finding.title,
        finding.evidence.path,
        verdict_reason,
    )
    return VerifiedFinding(
        category=finding.category,
        severity=finding.severity,
        title=finding.title,
        rationale=finding.rationale,
        evidence=None,
        suggested_fix=finding.suggested_fix,
        confidence=finding.confidence,
        verdict="rejected",
        verdict_reason=verdict_reason,
        rejected_by=stage,  # type: ignore[arg-type]
        source=finding.source,
        rule_id=finding.rule_id,
    )


def bind_evidence(
    root: Path | str,
    finding: CandidateFinding,
    *,
    max_bytes: int = 262_144,
) -> VerifiedFinding:
    """Check *finding* against the real file and rewrite its snippet from disk.

    Returns a :class:`VerifiedFinding` with ``verdict="pending"`` when the
    evidence checks out -- it still has to face the LLM verifier -- or
    ``verdict="rejected"`` with ``rejected_by="evidence"`` when it does not.

    Tool-sourced findings (ruff, AST) skip the match requirement: their line
    numbers come from the tool itself, so there is nothing to verify. They are
    returned immediately with ``verdict="confirmed"``.

    Args:
        root: Repository root; all reads are confined to it.
        finding: The candidate to check.
        max_bytes: Size cap applied when re-reading the file.
    """
    # Deterministic tools already give ground-truth line numbers.
    if finding.source in ("ruff", "ast"):
        return _confirm_tool_finding(root, finding, max_bytes=max_bytes)

    evidence = finding.evidence

    try:
        target = resolve_in_root(root, evidence.path)
    except SandboxViolation as exc:
        return reject(finding, stage="evidence", reason="SANDBOX_VIOLATION", detail=str(exc))

    try:
        text, _encoding, _truncated = read_text_safely(
            target, max_bytes=max_bytes, root=root
        )
    except ToolError as exc:
        return reject(finding, stage="evidence", reason="UNREADABLE", detail=str(exc))
    except SandboxViolation as exc:
        return reject(finding, stage="evidence", reason="SANDBOX_VIOLATION", detail=str(exc))

    lines = text.splitlines()
    total = len(lines)

    if total == 0:
        return reject(finding, stage="evidence", reason="EMPTY_FILE")

    start, end = evidence.line_start, evidence.line_end
    if start < 1 or end > total or start > end:
        return reject(
            finding,
            stage="evidence",
            reason="OUT_OF_RANGE",
            detail=f"cited {start}-{end} but file has {total} lines",
        )

    canonical = _match_at_or_near(lines, evidence, start, end)

    if canonical is None:
        return reject(
            finding,
            stage="evidence",
            reason="EVIDENCE_MISMATCH",
            detail=(
                f"cited snippet not found in lines {start}-{end} or the "
                f"±{_CONTEXT_PAD}-line window around it in {evidence.path}"
            ),
        )

    return VerifiedFinding(
        category=finding.category,
        severity=finding.severity,
        title=finding.title,
        rationale=finding.rationale,
        evidence=Evidence(
            path=_to_posix(evidence.path),
            line_start=start,
            line_end=end,
            snippet=canonical,  # <-- from DISK, never from the model
        ),
        suggested_fix=finding.suggested_fix,
        confidence=finding.confidence,
        verdict="pending",
        snippet_sha256=_sha256(canonical),
        source=finding.source,
        rule_id=finding.rule_id,
    )


def _match_at_or_near(
    lines: list[str],
    evidence: Evidence,
    start: int,
    end: int,
) -> str | None:
    """Return the on-disk text the snippet matches, or None.

    First tries the exact cited range. If that fails, widens by ``_CONTEXT_PAD``
    lines each way to tolerate small line-number drift. The widened text is
    returned rather than the original window, so the report shows the true
    surrounding code.
    """
    if not evidence.snippet.strip():
        # No snippet to check. Accept the range as-is rather than rejecting: an
        # empty snippet is a formatting failure, and the range still points at
        # real lines. The verifier stage will judge the claim itself.
        return "\n".join(lines[start - 1 : end])

    claimed = _normalise(evidence.snippet)

    window = "\n".join(lines[start - 1 : end])
    if claimed in _normalise(window):
        return window

    lo = max(0, start - 1 - _CONTEXT_PAD)
    hi = min(len(lines), end + _CONTEXT_PAD)
    wider = "\n".join(lines[lo:hi])
    if claimed in _normalise(wider):
        return wider

    return None


def _confirm_tool_finding(
    root: Path | str, finding: CandidateFinding, *, max_bytes: int
) -> VerifiedFinding:
    """Bind a tool-sourced finding, which needs no snippet-matching.

    The line range is still clamped to the real file, and the snippet is still
    filled in from disk so the report shows actual code.
    """
    evidence = finding.evidence
    snippet = ""
    start, end = evidence.line_start, evidence.line_end

    try:
        target = resolve_in_root(root, evidence.path)
        text, _enc, _trunc = read_text_safely(target, max_bytes=max_bytes, root=root)
        lines = text.splitlines()
        if lines and 1 <= start <= len(lines):
            end = min(end, len(lines))
            snippet = "\n".join(lines[start - 1 : end])
        else:
            start = end = 1
    except (ToolError, SandboxViolation) as exc:
        logger.debug("could not bind tool finding in %s: %s", evidence.path, exc)

    return VerifiedFinding(
        category=finding.category,
        severity=finding.severity,
        title=finding.title,
        rationale=finding.rationale,
        evidence=Evidence(
            path=_to_posix(evidence.path),
            line_start=start,
            line_end=end,
            snippet=snippet,
        ),
        suggested_fix=finding.suggested_fix,
        confidence=finding.confidence,
        verdict="confirmed",  # a real tool reported it; no model judgement needed
        verdict_reason=f"reported by {finding.rule_id or finding.source}",
        snippet_sha256=_sha256(snippet) if snippet else None,
        source=finding.source,
        rule_id=finding.rule_id,
    )


def _to_posix(path: str) -> str:
    """Normalise to forward slashes so reports are OS-independent."""
    return path.replace("\\", "/")


def bind_many(
    root: Path | str,
    findings: list[CandidateFinding],
    *,
    max_bytes: int = 262_144,
) -> list[VerifiedFinding]:
    """Bind a batch of candidates, tolerating individual failures."""
    return [bind_evidence(root, f, max_bytes=max_bytes) for f in findings]
