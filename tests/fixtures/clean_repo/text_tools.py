"""String helpers for the clean-repo fixture."""

from __future__ import annotations

from pathlib import Path

#: Longest input accepted by :func:`truncate`, to keep outputs bounded.
MAX_LENGTH = 120


def truncate(text: str, limit: int = MAX_LENGTH) -> str:
    """Return *text*, shortened to *limit* characters with an ellipsis.

    Raises:
        ValueError: if *limit* is smaller than the ellipsis itself.
    """
    ellipsis = "..."
    if limit < len(ellipsis):
        raise ValueError(f"limit must be at least {len(ellipsis)}")
    if len(text) <= limit:
        return text
    return text[: limit - len(ellipsis)] + ellipsis


def read_lines(path: Path) -> list[str]:
    """Read *path* and return its lines without terminators.

    Raises:
        OSError: if the file cannot be read.
    """
    with path.open(encoding="utf-8") as handle:
        return [line.rstrip("\n") for line in handle]


def count_words(text: str) -> dict[str, int]:
    """Return a word-frequency mapping for *text*, lower-cased."""
    counts: dict[str, int] = {}
    for word in text.lower().split():
        counts[word] = counts.get(word, 0) + 1
    return counts
