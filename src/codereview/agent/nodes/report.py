"""Terminal node: assemble the report payload and its statistics.

The statistics block is not decoration. It is how the assignment's "edge cases
handled" and "anti-hallucination" claims become checkable: a run showing
``candidates=7, confirmed=0, rejected_by_evidence=7`` on clean code says more
than any amount of prose about robustness.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ...models.findings import SEVERITY_ORDER
from ...models.report import ReviewReport, RunStats
from ..state import ReviewState, total_usage

logger = logging.getLogger(__name__)

_SEVERITY_WORDS = {
    "critical": "严重",
    "high": "高",
    "medium": "中",
    "low": "低",
    "info": "提示",
}


def _build_summary(files, reportable, abort_reason: str | None) -> str:
    """Compose the summary in Chinese.

    Built from the counters rather than asked of the model. A generated summary
    would be a *second* chance to state something untrue about a run whose whole
    design is about not doing that -- and the numbers are already available and
    exact. It would also cost a model call to restate arithmetic.

    Offline mode is deliberately *not* mentioned here: the renderers print a
    prominent warning of their own, and repeating it in the summary is noise.
    """
    if abort_reason:
        return abort_reason

    reviewed = len([f for f in files if f.reviewable])

    if not reportable:
        return f"共检查 {reviewed} 个文件，未发现问题。"

    counts: dict[str, int] = {}
    for finding in reportable:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1

    breakdown = "、".join(
        f"{_SEVERITY_WORDS.get(name, name)} {count} 条"
        for name, count in sorted(counts.items(), key=lambda item: SEVERITY_ORDER.get(item[0], 9))
    )

    files_hit = len({f.evidence.path for f in reportable if f.evidence})
    return (
        f"共检查 {reviewed} 个文件，在 {files_hit} 个文件中发现 "
        f"{len(reportable)} 个问题（{breakdown}）。"
    )


def report_node(state: ReviewState) -> dict:
    """Build the report and compute run statistics."""
    verified = state.get("verified") or state.get("candidates", [])
    files = state.get("files", [])
    usage = total_usage(state.get("usage", []))

    reportable = [f for f in verified if f.is_reportable]

    stats = RunStats(
        files_scanned=len(files),
        files_reviewed=len([f for f in files if f.reviewable]),
        files_skipped=len([f for f in files if f.skipped]),
        files_failed=usage.failed_files,
        llm_calls=usage.llm_calls,
        tool_calls=usage.tool_calls,
        candidates=len(verified),
        confirmed=sum(1 for f in verified if f.verdict == "confirmed"),
        uncertain=sum(1 for f in verified if f.verdict == "uncertain"),
        rejected_by_evidence=sum(1 for f in verified if f.rejected_by == "evidence"),
        rejected_by_verifier=sum(1 for f in verified if f.rejected_by == "verifier"),
        malformed_responses=usage.malformed_responses,
        prompt_cache_hit_tokens=usage.cache_read_tokens,
        prompt_cache_miss_tokens=usage.cache_miss_tokens,
        output_tokens=usage.output_tokens,
        started_at=state.get("started_at"),
        finished_at=datetime.now(),
    )

    abort_reason = state.get("abort_reason")

    report = ReviewReport(
        repo_root=state.get("repo_root", ""),
        run_id=state.get("run_id", ""),
        findings=[*reportable, *[f for f in verified if not f.is_reportable]],
        stats=stats,
        summary=_build_summary(files, reportable, abort_reason),
        aborted=bool(abort_reason),
        abort_reason=abort_reason,
        offline=bool(state.get("offline")),
        errors=list(state.get("errors", [])),
    )

    logger.info(
        "report: %d finding(s) across %d file(s); %d rejected by the pipeline",
        len(reportable),
        len(report.files_touched),
        stats.total_rejected,
    )

    return {"report": report, "verified": verified}
