"""Split oversized files into overlapping review windows.

A 5,000-line file will not fit in one prompt alongside the reviewer's
instructions and tool schema, and truncating it silently would mean the tail is
never reviewed while the report claims full coverage. Instead the file is split
into windows with a small overlap, and each window becomes its own review task.

The overlap matters: a defect that straddles a boundary (a function signature on
the last line of one window, its body in the next) would otherwise be invisible
to both.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Lines per window. Roughly 4k tokens of Python -- comfortably inside any
#: modern context window once the prompt and tool schema are added.
DEFAULT_WINDOW_LINES = 400

#: Lines shared between consecutive windows.
DEFAULT_OVERLAP_LINES = 40

#: Files longer than this are split rather than sent whole.
SPLIT_THRESHOLD_LINES = 450


@dataclass(frozen=True, slots=True)
class Chunk:
    """One review window within a file."""

    line_start: int
    line_end: int
    index: int
    total: int

    @property
    def is_partial(self) -> bool:
        return self.total > 1


def chunk_lines(
    line_count: int,
    *,
    window: int = DEFAULT_WINDOW_LINES,
    overlap: int = DEFAULT_OVERLAP_LINES,
    threshold: int = SPLIT_THRESHOLD_LINES,
) -> list[Chunk]:
    """Return the windows a file of *line_count* lines should be reviewed in.

    Files at or below *threshold* get a single chunk covering everything, so the
    common case costs nothing extra.

    Raises:
        ValueError: *overlap* is not smaller than *window*, which would loop forever.
    """
    if line_count <= 0:
        return []
    if line_count <= threshold:
        return [Chunk(1, line_count, 0, 1)]
    if overlap >= window:
        raise ValueError(f"overlap ({overlap}) must be smaller than window ({window})")

    stride = window - overlap
    chunks: list[Chunk] = []

    start = 1
    while start <= line_count:
        end = min(start + window - 1, line_count)
        chunks.append(Chunk(start, end, len(chunks), 0))
        if end >= line_count:
            break
        start += stride

    total = len(chunks)
    return [Chunk(c.line_start, c.line_end, c.index, total) for c in chunks]
