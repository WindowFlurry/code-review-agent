"""Filesystem tools: ``list_files`` and ``read_file``.

Written as plain functions taking an explicit ``root`` rather than as LangChain
tools. That keeps the deterministic core unit-testable with no LLM stack
installed, and lets :mod:`codereview.tools.registry` do the ``@tool`` wrapping.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..errors import ToolError
from .sandbox import read_text_safely, resolve_in_root

logger = logging.getLogger(__name__)

#: Directories that never contain project source worth reviewing.
IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        "build",
        "dist",
        ".eggs",
        "site-packages",
        ".tox",
        ".idea",
        ".vscode",
    }
)

#: Files that are noise in a review.
IGNORED_SUFFIXES = frozenset(
    {".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe", ".bin", ".o", ".a", ".class", ".jar"}
)


def _is_ignored(path: Path) -> bool:
    return any(part in IGNORED_DIRS for part in path.parts) or path.suffix.lower() in IGNORED_SUFFIXES


def list_files(
    root: Path | str,
    subdir: str = ".",
    glob: str | None = None,
    max_entries: int = 500,
) -> list[str]:
    """List reviewable files under *subdir*, as repo-relative POSIX paths.

    The agent is given this instead of guessing paths, so a finding can never
    cite a file that does not exist.

    Args:
        root: Repository root.
        subdir: Subdirectory to list, relative to *root*.
        glob: Optional fnmatch-style filter applied to the filename, e.g. ``*.py``.
        max_entries: Cap on returned paths, so a huge tree cannot flood the context.

    Raises:
        SandboxViolation: *subdir* escapes *root*.
        ToolError: *subdir* is not a directory.
    """
    base = resolve_in_root(root, subdir)

    if not base.exists():
        raise ToolError(f"no such directory: {subdir}")
    if not base.is_dir():
        raise ToolError(f"not a directory: {subdir}")

    root_resolved = Path(root).resolve()
    pattern = glob or "*"
    found: list[str] = []

    for entry in sorted(base.rglob(pattern)):
        if len(found) >= max_entries:
            logger.debug("list_files hit max_entries=%d under %s", max_entries, subdir)
            break
        if not entry.is_file():
            continue
        try:
            relative = entry.resolve().relative_to(root_resolved)
        except ValueError:
            continue  # symlink pointing outside the repo
        if _is_ignored(relative):
            continue
        found.append(relative.as_posix())

    return found


def read_file(
    root: Path | str,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    max_bytes: int = 262_144,
) -> str:
    """Read a file and return it with ``NNNN: `` line-number prefixes.

    The prefixes are what let the model cite accurate line numbers. They are
    stripped again during evidence binding, so a model that copies them into its
    snippet still matches.

    Args:
        root: Repository root.
        path: File to read, relative to *root*.
        start_line: First line to return (1-based, inclusive).
        end_line: Last line to return (inclusive).
        max_bytes: Read cap; larger files are truncated with an explicit marker.

    Raises:
        SandboxViolation: *path* escapes *root*.
        ToolError: missing, binary, or unreadable file.
    """
    target = resolve_in_root(root, path)
    text, encoding, truncated = read_text_safely(target, max_bytes=max_bytes, root=root)

    lines = text.splitlines()
    total = len(lines)

    lo = max(1, start_line or 1)
    hi = min(total, end_line or total)

    if lo > total:
        raise ToolError(f"{path} has only {total} lines; line {lo} does not exist")

    selected = lines[lo - 1 : hi]
    width = len(str(hi))

    body = "\n".join(f"{lo + i:>{width}}: {line}" for i, line in enumerate(selected))

    header = f"# {path} (lines {lo}-{hi} of {total}, encoding={encoding})"
    if truncated:
        header += f"\n# [TRUNCATED: only the first {max_bytes} bytes were read]"

    return f"{header}\n{body}"


def file_stats(root: Path | str, path: str, max_bytes: int = 262_144) -> dict[str, object]:
    """Cheap metadata for a file, used during ingestion rather than by the agent."""
    target = resolve_in_root(root, path)
    if not target.is_file():
        raise ToolError(f"not a regular file: {path}")

    size = target.stat().st_size
    text, encoding, truncated = read_text_safely(target, max_bytes=max_bytes, root=root)

    return {
        "path": target.resolve().relative_to(Path(root).resolve()).as_posix(),
        "size_bytes": size,
        "line_count": len(text.splitlines()),
        "encoding": encoding,
        "truncated": truncated,
    }
