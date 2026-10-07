"""Walk a repository and produce a deterministic inventory.

Everything here runs before any token is spent. The inventory is ground truth
that both the planner and the report rely on, so it must never be produced by a
model: if the inventory is wrong, every downstream citation is wrong.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from ..errors import IngestionError, SandboxViolation, ToolError
from ..models.plan import FileMeta
from ..tools.fs_tools import IGNORED_DIRS, IGNORED_SUFFIXES
from ..tools.sandbox import decode_text, looks_binary, read_text_safely
from .classify import detect_language, is_generated, is_vendored

logger = logging.getLogger(__name__)

#: Only files at or below this size are read in full during ingestion.
_STAT_SNIFF_BYTES = 8192


def walk_repo(
    root: Path | str,
    *,
    max_files: int = 40,
    max_file_bytes: int = 262_144,
    languages: tuple[str, ...] = ("python",),
    only_files: tuple[str, ...] | None = None,
) -> list[FileMeta]:
    """Inventory *root*, returning reviewable files first.

    Files are never dropped for being unusual -- they are marked ``skipped`` with
    a reason, so the report can say "3 files skipped: 1 binary, 2 too large"
    instead of silently ignoring them.

    Args:
        root: Repository root.
        max_files: Cap on *reviewable* files returned. Skipped files are not counted.
        max_file_bytes: Files above this are marked ``too_large``.
        languages: Languages considered reviewable.
        only_files: Restrict the inventory to these repo-relative paths. Used to
            review a single named file, whose *parent* is the sandbox root.

    Raises:
        IngestionError: *root* is missing or not a directory.
        SandboxViolation: *root* cannot be resolved.
    """
    # The root itself is resolved directly, NOT via resolve_in_root: that helper
    # resolves a path *inside* a root, and passing the root to it would produce
    # `<root>/<root>`.
    try:
        root_path = Path(root).resolve()
    except (OSError, RuntimeError) as exc:
        raise IngestionError(f"could not resolve repository root {root!r}: {exc}") from exc

    if not root_path.exists():
        raise IngestionError(f"repository root does not exist: {root}")
    if not root_path.is_dir():
        raise IngestionError(f"not a directory: {root}")

    reviewable: list[FileMeta] = []
    skipped: list[FileMeta] = []

    for entry, relative in _iter_candidates(root_path, only_files):
        meta = _inspect(
            root_path, entry, relative, max_file_bytes=max_file_bytes, languages=languages
        )

        if meta.skipped:
            skipped.append(meta)
        elif len(reviewable) < max_files:
            reviewable.append(meta)
        elif len(reviewable) == max_files:
            # Record that the cap was hit rather than pretending we saw everything.
            skipped.append(
                meta.model_copy(
                    update={
                        "skipped": True,
                        "skip_reason": "too_large",
                    }
                )
            )

    if len(reviewable) == max_files:
        logger.warning(
            "max_files=%d reached; some files were not scheduled for review", max_files
        )

    logger.info(
        "inventory: %d reviewable, %d skipped (of %d files)",
        len(reviewable),
        len(skipped),
        len(reviewable) + len(skipped),
    )
    return reviewable + skipped


def _iter_candidates(
    root: Path,
    only_files: tuple[str, ...] | None,
):
    """Yield ``(absolute_path, relative_path)`` pairs worth inspecting.

    Two things here matter more than they look:

    ``only_files`` short-circuits the walk entirely. Without that, reviewing one
    named file still traverses its whole parent directory -- and a Desktop with a
    virtualenv on it held 590,000 files, so the "single file" case took minutes
    and looked like a hang.

    Otherwise the traversal uses ``os.walk`` and prunes ``dirnames`` *in place*.
    ``Path.rglob`` cannot do this: it descends into ``.venv`` and
    ``node_modules`` first and only then lets the caller discard them, so the
    expensive directories are paid for regardless. Pruning skips them outright.
    """
    if only_files is not None:
        for name in only_files:
            candidate = root / name
            if candidate.is_file():
                yield candidate, Path(name)
        return

    for dirpath, dirnames, filenames in os.walk(root):
        # In-place mutation is how os.walk is told not to descend.
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)

        for filename in sorted(filenames):
            if Path(filename).suffix.lower() in IGNORED_SUFFIXES:
                continue

            entry = Path(dirpath) / filename
            try:
                relative = entry.resolve().relative_to(root)
            except (ValueError, OSError):
                continue  # symlink pointing outside the root
            yield entry, relative


def _inspect(
    root: Path,
    entry: Path,
    relative: Path,
    *,
    max_file_bytes: int,
    languages: tuple[str, ...],
) -> FileMeta:
    """Build a :class:`FileMeta` for one file, marking it skipped if unfit.

    ``entry`` is the absolute path on disk; ``relative`` is only the label used
    for reporting and for sandbox checks. Conflating the two is a real bug --
    resolving a repo-relative path against the process CWD silently makes every
    file look unreadable.
    """
    posix = relative.as_posix()
    language = detect_language(relative)

    meta = FileMeta(path=posix, language=language)

    if language not in languages:
        return meta.model_copy(update={"skipped": True, "skip_reason": "not_python"})

    if is_vendored(relative):
        return meta.model_copy(update={"skipped": True, "skip_reason": "vendored"})

    try:
        size = entry.stat().st_size
    except OSError as exc:
        logger.debug("stat failed for %s: %s", posix, exc)
        return meta.model_copy(update={"skipped": True, "skip_reason": "unreadable"})

    meta = meta.model_copy(update={"size_bytes": size})

    if size == 0:
        return meta.model_copy(update={"skipped": True, "skip_reason": "empty"})

    if size > max_file_bytes:
        return meta.model_copy(update={"skipped": True, "skip_reason": "too_large"})

    try:
        with entry.open("rb") as fh:
            head = fh.read(_STAT_SNIFF_BYTES)
    except OSError as exc:
        logger.debug("read failed for %s: %s", posix, exc)
        return meta.model_copy(update={"skipped": True, "skip_reason": "unreadable"})

    if looks_binary(head):
        return meta.model_copy(update={"skipped": True, "skip_reason": "binary"})

    if is_generated(decode_text(head)[0]):
        return meta.model_copy(update={"skipped": True, "skip_reason": "generated"})

    try:
        text, encoding, truncated = read_text_safely(
            entry, max_bytes=max_file_bytes, root=root
        )
    except (ToolError, SandboxViolation) as exc:
        logger.debug("could not read %s: %s", posix, exc)
        return meta.model_copy(update={"skipped": True, "skip_reason": "unreadable"})

    return meta.model_copy(
        update={
            "line_count": len(text.splitlines()),
            "encoding": encoding,
            "truncated": truncated,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
    )
