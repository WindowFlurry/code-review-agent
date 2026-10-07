"""Reduce step: deduplicate and order the findings.

Two reviewers covering overlapping windows of a large file, or a lint finding
that echoes an AST finding, will report the same defect more than once. Dedup
runs here, after all branches have merged, so it sees the complete picture
rather than one branch's view.
"""

from __future__ import annotations

import logging

from ...models.findings import SEVERITY_ORDER
from ...verification.dedup import deduplicate
from ..state import ReviewState

logger = logging.getLogger(__name__)

#: Findings reported per run, worst first. Beyond this the report stops being
#: readable and starts being a wall of text nobody acts on.
MAX_REPORTED = 200


def aggregate_node(state: ReviewState) -> dict:
    """Deduplicate, sort by severity, and cap the finding list."""
    verified = state.get("verified") or state.get("candidates", [])

    if not verified:
        return {"verified": []}

    reportable = [f for f in verified if f.is_reportable]
    rejected = [f for f in verified if not f.is_reportable]

    deduped = deduplicate(reportable)

    dropped = len(reportable) - len(deduped)
    if dropped:
        logger.info("aggregate: merged %d duplicate finding(s)", dropped)

    ordered = sorted(deduped, key=lambda f: f.sort_key)

    if len(ordered) > MAX_REPORTED:
        logger.warning(
            "aggregate: truncating %d finding(s) to %d", len(ordered), MAX_REPORTED
        )
        ordered = ordered[:MAX_REPORTED]

    logger.info(
        "aggregate: %d reported, %d rejected by the pipeline", len(ordered), len(rejected)
    )

    return {"verified": [*ordered, *rejected]}


def severity_counts(findings) -> dict[str, int]:
    """Count findings per severity, for the report header."""
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: SEVERITY_ORDER.get(item[0], 9)))
