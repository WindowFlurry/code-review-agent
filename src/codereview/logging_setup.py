"""Logging: human-readable on the console, JSONL on disk.

The on-disk JSONL is the *evidence trail* for a run. When a review reports
something surprising, ``runs/<id>/agent.log`` shows which tool returned what.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

#: Third-party loggers that are noisy at INFO and say nothing we need.
_QUIET = ("httpx", "httpcore", "openai", "urllib3", "asyncio")


class _JsonlFormatter(logging.Formatter):
    """One JSON object per line -- greppable and machine-readable."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(
    run_dir: Path | None = None,
    *,
    verbose: bool = False,
    quiet: bool = False,
) -> Path | None:
    """Install console + optional file handlers. Safe to call more than once.

    Args:
        run_dir: Directory for the JSONL log. ``None`` skips file logging.
        verbose: DEBUG on the console.
        quiet: WARNING on the console. The file log still records everything --
            silencing the console should not mean losing the evidence trail.

    Returns:
        The path of the JSONL log, or ``None`` if no *run_dir* was given.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console_level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(console_level)
    try:
        from rich.logging import RichHandler

        console = RichHandler(rich_tracebacks=True, show_path=verbose, markup=False)
        console.setLevel(console_level)
    except ImportError:  # rich is optional at import time
        console.setFormatter(logging.Formatter("%(levelname)-8s %(name)s: %(message)s"))
    root.addHandler(console)

    log_path: Path | None = None
    if run_dir is not None:
        run_dir.mkdir(parents=True, exist_ok=True)
        log_path = run_dir / "agent.log"
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(_JsonlFormatter())
        root.addHandler(file_handler)

    for name in _QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)

    return log_path


def new_run_dir(base: Path, *, label: str = "review") -> Path:
    """Create a timestamped run directory, e.g. ``runs/20260930-142530-review``."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = base / f"{stamp}-{label}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir
