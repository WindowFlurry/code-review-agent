"""Offline mode and the missing-key guard.

These exist because of a real defect: without an API key, every LLM node failed
individually, each failure was swallowed to keep the run alive, and the result
was a perfectly ordinary-looking report built only from ruff and AST findings.
A reader had no way to tell it apart from a complete review.

The fix has two halves, and both are tested here: a missing key is now a hard
error, and the one legitimate way to skip the LLM stages is explicit, labelled in
every output format, and provably makes no model calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codereview.agent.graph import build_graph
from codereview.agent.state import empty_state
from codereview.config import Settings
from codereview.errors import ConfigError
from codereview.report import render_json, render_markdown
from codereview.runner import run_review


@pytest.fixture
def no_key_settings(tmp_path: Path) -> Settings:
    """Settings with no credentials, isolated from any real .env on this machine."""
    return Settings(
        _env_file=None,
        deepseek_api_key="",
        run_dir=tmp_path / "runs",
    )


@pytest.fixture
def stub_settings(tmp_path: Path) -> Settings:
    """Settings with a placeholder key, to get past the guard."""
    return Settings(
        _env_file=None,
        deepseek_api_key="sk-not-a-real-key",
        run_dir=tmp_path / "runs",
    )


def test_review_without_a_key_is_a_hard_error(buggy_repo: Path, no_key_settings) -> None:
    """Silently degrading to a partial review is worse than failing."""
    with pytest.raises(ConfigError) as excinfo:
        run_review(target=buggy_repo, settings=no_key_settings, formats=[])

    message = str(excinfo.value)
    assert "DEEPSEEK_API_KEY" in message
    assert "--offline" in message, "the error must say how to proceed"


def test_offline_review_succeeds_without_a_key(buggy_repo: Path, no_key_settings) -> None:
    report = run_review(
        target=buggy_repo, settings=no_key_settings, offline=True, formats=[]
    )

    assert report.offline is True
    assert not report.aborted
    # The deterministic layer still finds the ruff and AST defects.
    assert report.reportable


def test_offline_review_makes_no_model_calls(buggy_repo: Path, no_key_settings) -> None:
    report = run_review(
        target=buggy_repo, settings=no_key_settings, offline=True, formats=[]
    )

    assert report.stats.llm_calls == 0
    assert report.stats.tool_calls == 0


def test_offline_review_still_reports_evidence_backed_findings(buggy_repo, no_key_settings) -> None:
    """Deterministic findings are bound to real code like any other."""
    report = run_review(
        target=buggy_repo, settings=no_key_settings, offline=True, formats=[]
    )

    for finding in report.reportable:
        assert finding.evidence is not None
        assert finding.evidence.snippet, "every reported finding carries real code"


def test_offline_report_is_labelled_in_markdown(buggy_repo, no_key_settings) -> None:
    report = run_review(
        target=buggy_repo, settings=no_key_settings, offline=True, formats=[]
    )

    text = render_markdown(report)

    assert "本次只做了确定性检查" in text
    assert "不在" in text and "范围内" in text


def test_offline_report_is_labelled_in_json(buggy_repo, no_key_settings) -> None:
    import json

    report = run_review(
        target=buggy_repo, settings=no_key_settings, offline=True, formats=[]
    )
    payload = json.loads(render_json(report))

    assert payload["offline"] is True


def test_non_offline_report_is_not_labelled_offline(buggy_repo, no_key_settings) -> None:
    """The label must be absent, not merely false-looking, on a full run."""
    report = run_review(
        target=buggy_repo, settings=no_key_settings, offline=True, formats=[]
    )
    report.offline = False

    assert "本次只做了确定性检查" not in render_markdown(report)


def test_graph_offline_state_produces_no_review_tasks(buggy_repo: Path) -> None:
    """The planner must not fall back to 'review every file' in offline mode.

    A non-empty task list would send the fan-out looking for a reviewer that was
    never built -- which is exactly how the original silent degradation worked.
    """
    state = build_graph().invoke(
        empty_state(str(buggy_repo), "offline", offline=True),
        {"configurable": {"thread_id": "offline"}},
    )

    assert state["tasks"] == []
    assert state["report"].offline is True


def test_graph_offline_still_collects_deterministic_findings(buggy_repo: Path) -> None:
    state = build_graph().invoke(
        empty_state(str(buggy_repo), "offline-effects", offline=True),
        {"configurable": {"thread_id": "offline-effects"}},
    )

    report = state["report"]
    assert report.reportable
    assert {f.source for f in report.reportable} <= {"ruff", "ast"}
