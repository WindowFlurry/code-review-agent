"""JSON report -- the machine-readable artefact.

Emits both the reported findings and the *rejected* ones. Keeping the rejects is
the point: they are the audit trail showing what the anti-hallucination gate
caught, and dropping them would make the pipeline's main guarantee unverifiable
after the fact.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models.report import ReviewReport


def render_json(report: ReviewReport, *, indent: int = 2) -> str:
    """Serialise the report, including rejected findings."""
    payload = {
        "schema_version": 1,
        "repo_root": report.repo_root,
        "run_id": report.run_id,
        "aborted": report.aborted,
        "abort_reason": report.abort_reason,
        "offline": report.offline,
        "summary": report.summary,
        "findings": [
            {
                **finding.model_dump(mode="json"),
                "reported": finding.is_reportable,
            }
            for finding in report.findings
        ],
        "stats": report.stats.model_dump(mode="json"),
        "errors": report.errors,
    }
    return json.dumps(payload, indent=indent, ensure_ascii=False)


def write_json(report: ReviewReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_json(report), encoding="utf-8")
    return path
