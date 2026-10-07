"""Checkpointer selection.

Two flavours, and the difference is the whole point of the memory requirement:

``InMemorySaver``  fast, but the thread dies with the process.
``SqliteSaver``    survives a restart -- which is what makes it demonstrable.
                   Kill the process, start a new one, ask a follow-up question
                   about the review, and it answers.

Both are constructed from an open connection rather than ``from_conn_string``.
That helper is a context manager, so the connection would close as soon as the
helper returned and every subsequent checkpoint write would fail against a dead
handle. Owning the connection here keeps it alive for the process's lifetime.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def get_checkpointer(db_path: Path | str | None = None):
    """Return a checkpointer, persisting to *db_path* when one is given.

    Args:
        db_path: SQLite file to persist threads to. ``None`` returns an
            in-memory saver.

    Returns:
        A LangGraph checkpointer instance.

    Raises:
        RuntimeError: the SQLite extra is not installed.
    """
    if db_path is None:
        from langgraph.checkpoint.memory import InMemorySaver

        logger.debug("using InMemorySaver (threads will not survive this process)")
        return InMemorySaver()

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:  # pragma: no cover - depends on install
        raise RuntimeError(
            "SQLite persistence needs langgraph-checkpoint-sqlite: "
            "pip install langgraph-checkpoint-sqlite"
        ) from exc

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # check_same_thread=False is required: verification runs on a thread pool,
    # and SQLite rejects cross-thread use of a connection by default.
    connection = sqlite3.connect(str(path), check_same_thread=False)
    logger.debug("using SqliteSaver at %s", path)
    return SqliteSaver(connection)
