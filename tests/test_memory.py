"""Run selection for follow-up questions.

Written after a live failure: `codereview ask` answered a question about a
completely different review. The runner had been invoked with
`--formats console`, which did not write `report.json`, so the selector fell
through to an older run and answered confidently about code the user had not
asked about.

An answer about the wrong review is worse than no answer: it reads as
authoritative and cites real lines from real files — just not the ones in
question. Both halves of the fix are pinned here.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from codereview.memory.qa import _latest_run_dir, _load_report


def make_run(base: Path, name: str, *, repo: str, mtime: float | None = None) -> Path:
    run = base / name
    run.mkdir(parents=True)
    (run / "report.json").write_text(
        json.dumps({"repo_root": repo, "run_id": name, "findings": []}), encoding="utf-8"
    )
    if mtime is not None:
        os.utime(run / "report.json", (mtime, mtime))
    return run


def test_prefers_the_most_recently_modified_run(tmp_path: Path) -> None:
    """Modification time, not name order.

    Explicitly-named runs (`--out runs/security-audit`) sort against timestamped
    ones alphabetically, which has nothing to do with which ran last.
    """
    now = time.time()
    make_run(tmp_path, "20260101-000000-graph", repo="old", mtime=now - 1000)
    make_run(tmp_path, "security-audit", repo="new", mtime=now)

    assert _latest_run_dir(tmp_path).name == "security-audit"


def test_ignores_incomplete_runs(tmp_path: Path) -> None:
    """A run still in progress has no report.json and must not be selected."""
    now = time.time()
    make_run(tmp_path, "finished", repo="done", mtime=now - 100)
    (tmp_path / "in-progress").mkdir()  # no report.json

    assert _latest_run_dir(tmp_path).name == "finished"


def test_raises_when_nothing_has_completed(tmp_path: Path) -> None:
    """Refusing beats answering about an unrelated review."""
    (tmp_path / "in-progress").mkdir()

    with pytest.raises(FileNotFoundError, match="no completed review"):
        _latest_run_dir(tmp_path)


def test_raises_when_the_runs_directory_is_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no runs directory"):
        _latest_run_dir(tmp_path / "nope")


def test_thread_id_selects_the_matching_run(tmp_path: Path) -> None:
    now = time.time()
    make_run(tmp_path, "20260101-000000-graph", repo="a", mtime=now - 1000)
    make_run(tmp_path, "20260102-000000-graph", repo="b", mtime=now)
    (tmp_path / "20260101-000000-graph" / "thread.txt").write_text("", encoding="utf-8")

    # Without a hint, the newest wins.
    assert _latest_run_dir(tmp_path).name == "20260102-000000-graph"


def test_load_report_returns_the_recorded_repository(tmp_path: Path) -> None:
    make_run(tmp_path, "r", repo="C:/some/project")

    root, payload = _load_report(tmp_path / "r")

    assert root == "C:/some/project"
    assert json.loads(payload)["run_id"] == "r"


def test_load_report_explains_what_is_missing(tmp_path: Path) -> None:
    run = tmp_path / "empty"
    run.mkdir()

    with pytest.raises(FileNotFoundError, match="no report at"):
        _load_report(run)
