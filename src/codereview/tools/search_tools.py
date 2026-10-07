"""Code search via ripgrep.

Lets the reviewer check how a symbol is *used* elsewhere before calling it a bug
-- the difference between "this function has an unused parameter" and "this
parameter is required by the interface". Falls back to a pure-Python scan when
``rg`` is not installed, so the tool never hard-fails on a machine without it.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..errors import SandboxViolation, ToolError
from .sandbox import decode_text, looks_binary, resolve_in_root

logger = logging.getLogger(__name__)

_TIMEOUT_S = 15
_MAX_PATTERN_LEN = 500
_MAX_FILESIZE = "1M"


@dataclass(frozen=True, slots=True)
class SearchMatch:
    """One hit: where it is and what the line says."""

    path: str
    line_number: int
    line: str


def _ripgrep_path() -> str | None:
    return shutil.which("rg")


def search_code(
    root: Path | str,
    pattern: str,
    glob: str = "*.py",
    max_results: int = 50,
    case_sensitive: bool = False,
) -> list[SearchMatch]:
    """Search for *pattern* across the repository.

    Args:
        root: Repository root.
        pattern: Regular expression to search for.
        glob: File filter passed to ripgrep.
        max_results: Cap on returned matches.
        case_sensitive: When false, uses ``-i``.

    Raises:
        ToolError: ripgrep is unavailable *and* the fallback scan failed, or the
            pattern is malformed.
    """
    if not pattern:
        raise ToolError("empty search pattern")
    if len(pattern) > _MAX_PATTERN_LEN:
        raise ToolError(f"pattern too long ({len(pattern)} chars, max {_MAX_PATTERN_LEN})")

    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise ToolError(f"not a directory: {root}")

    rg = _ripgrep_path()
    if rg is None:
        logger.debug("ripgrep not found; falling back to a Python scan")
        return _python_search(root_path, pattern, glob, max_results, case_sensitive)

    argv = [
        rg,
        "--json",
        "--no-heading",
        "--max-filesize",
        _MAX_FILESIZE,
        "--max-count",
        str(max_results),
        "--glob",
        glob,
    ]
    if not case_sensitive:
        argv.append("--ignore-case")
    argv += ["--", pattern, "."]

    try:
        # shell=False throughout: the pattern is model-supplied and must never
        # reach a shell interpreter.
        proc = subprocess.run(
            argv,
            cwd=root_path,
            capture_output=True,
            timeout=_TIMEOUT_S,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"search timed out after {_TIMEOUT_S}s") from exc
    except OSError as exc:
        raise ToolError(f"could not run ripgrep: {exc}") from exc

    # ripgrep exits 1 when there are simply no matches; that is not an error.
    if proc.returncode not in (0, 1):
        stderr = decode_text(proc.stderr)[0].strip()
        raise ToolError(f"ripgrep failed (exit {proc.returncode}): {stderr[:300]}")

    return _parse_ripgrep_json(proc.stdout, max_results)


def _parse_ripgrep_json(raw: bytes, max_results: int) -> list[SearchMatch]:
    """Extract match records from ripgrep's newline-delimited JSON stream."""
    text, _encoding = decode_text(raw)
    matches: list[SearchMatch] = []

    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        if record.get("type") != "match":
            continue

        data = record.get("data", {})
        path_field = data.get("path", {})
        path = path_field.get("text") or path_field.get("bytes") or ""
        content = data.get("lines", {}).get("text", "")

        matches.append(
            SearchMatch(
                path=path.replace("\\", "/").lstrip("./"),
                line_number=int(data.get("line_number", 0)),
                line=content.rstrip("\n"),
            )
        )
        if len(matches) >= max_results:
            break

    return matches


def _python_search(
    root: Path,
    pattern: str,
    glob: str,
    max_results: int,
    case_sensitive: bool,
) -> list[SearchMatch]:
    """Fallback scan used only when ripgrep is not installed."""
    import re

    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(pattern, flags)
    except re.error as exc:
        raise ToolError(f"invalid regex {pattern!r}: {exc}") from exc

    from .fs_tools import list_files

    matches: list[SearchMatch] = []
    for relative in list_files(root, glob=glob, max_entries=2000):
        try:
            target = resolve_in_root(root, relative)
            data = target.read_bytes()[: 1 << 20]
        except (SandboxViolation, ToolError, OSError):
            continue
        if looks_binary(data):
            continue

        for number, line in enumerate(decode_text(data)[0].splitlines(), start=1):
            if regex.search(line):
                matches.append(SearchMatch(relative, number, line))
                if len(matches) >= max_results:
                    return matches

    return matches


def format_matches(matches: list[SearchMatch], *, limit: int = 50) -> str:
    """Render matches for the model, truncated with an explicit marker."""
    if not matches:
        return "（没有找到匹配项）"

    shown = matches[:limit]
    body = "\n".join(f"{m.path}:{m.line_number}: {m.line}" for m in shown)

    if len(matches) > limit:
        body += f"\n[TRUNCATED: showing {limit} of {len(matches)} matches]"
    return body
