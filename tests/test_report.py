"""Report rendering.

The JSON report is a deliverable in its own right, so its schema is pinned here.
In particular, rejected findings must be present in the output: they are the
audit trail proving the anti-hallucination gate ran, and dropping them would
make the pipeline's main guarantee unverifiable after the fact.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from codereview.models.findings import Evidence, VerifiedFinding
from codereview.models.report import ReviewReport, RunStats
from codereview.report import render_json, render_markdown, write_json, write_markdown


def _report() -> ReviewReport:
    started = datetime(2026, 9, 30, 12, 0, 0)
    confirmed = VerifiedFinding(
        category="security",
        severity="critical",
        title="eval() on untrusted input",
        rationale="Arbitrary code execution.",
        evidence=Evidence(
            path="app.py", line_start=17, line_end=17, snippet="    return eval(expression)"
        ),
        suggested_fix="use ast.literal_eval",
        confidence=0.9,
        verdict="confirmed",
        snippet_sha256="deadbeef",
    )
    rejected = VerifiedFinding(
        category="bug",
        severity="medium",
        title="invented defect",
        rationale="not real",
        verdict="rejected",
        rejected_by="evidence",
        verdict_reason="EVIDENCE_MISMATCH",
    )
    return ReviewReport(
        repo_root="C:/proj",
        run_id="demo",
        findings=[confirmed, rejected],
        summary="One critical issue.",
        stats=RunStats(
            files_scanned=3,
            files_reviewed=2,
            files_skipped=1,
            candidates=2,
            confirmed=1,
            rejected_by_evidence=1,
            llm_calls=5,
            tool_calls=9,
            prompt_cache_hit_tokens=1000,
            prompt_cache_miss_tokens=2000,
            output_tokens=400,
            started_at=started,
            finished_at=started + timedelta(seconds=12),
        ),
    )


def test_markdown_contains_findings_and_location() -> None:
    text = render_markdown(_report())

    assert "# 代码审查报告" in text
    # Code, paths and identifiers stay in English even though the prose is
    # Chinese -- a translated identifier would be worse than useless.
    assert "eval() on untrusted input" in text
    assert "app.py:17" in text
    assert "ast.literal_eval" in text


def test_markdown_stats_block_totals() -> None:
    text = render_markdown(_report())

    assert "因证据对不上被丢弃 | 1" in text
    assert "已检查文件 | 2" in text


def test_markdown_omits_rejected_findings_from_the_body() -> None:
    text = render_markdown(_report())

    assert "invented defect" not in text


def test_markdown_reports_clean_result_plainly() -> None:
    report = _report()
    report.findings = []

    text = render_markdown(report)

    assert "未发现问题" in text
    # A clean report claiming success must not invent a section of results.
    assert "eval()" not in text


def test_json_is_valid_and_includes_rejected() -> None:
    payload = json.loads(render_json(_report()))

    assert payload["schema_version"] == 1
    assert payload["repo_root"] == "C:/proj"
    assert len(payload["findings"]) == 2

    reported = [f for f in payload["findings"] if f["reported"]]
    rejected = [f for f in payload["findings"] if not f["reported"]]
    assert len(reported) == 1
    assert len(rejected) == 1
    assert rejected[0]["rejected_by"] == "evidence"


def test_json_records_the_evidence_hash() -> None:
    payload = json.loads(render_json(_report()))
    confirmed = next(f for f in payload["findings"] if f["reported"])

    assert confirmed["snippet_sha256"] == "deadbeef"
    assert confirmed["evidence"]["snippet"] == "    return eval(expression)"


def test_json_handles_an_aborted_run() -> None:
    report = _report()
    report.aborted = True
    report.abort_reason = "no reviewable Python files found"
    report.findings = []

    payload = json.loads(render_json(report))

    assert payload["aborted"] is True
    assert payload["abort_reason"] == "no reviewable Python files found"


def test_writers_create_parent_directories(tmp_path) -> None:
    report = _report()

    md_path = write_markdown(report, tmp_path / "nested" / "report.md")
    json_path = write_json(report, tmp_path / "nested" / "report.json")

    assert md_path.is_file()
    assert json_path.is_file()
    assert json.loads(json_path.read_text(encoding="utf-8"))["run_id"] == "demo"


def test_markdown_survives_non_ascii(tmp_path) -> None:
    """Chinese source comments must not corrupt the report on a GBK console."""
    report = _report()
    report.findings[0].rationale = "中文注释：这里存在注入风险"

    text = render_markdown(report)
    path = write_markdown(report, tmp_path / "report.md")

    assert "中文注释" in text
    assert "中文注释" in path.read_text(encoding="utf-8")
