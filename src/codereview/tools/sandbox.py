"""Path sandboxing and file-safety guards.

Every tool that accepts a path from the model funnels through :func:`resolve_in_root`.
The model is an untrusted input source: it can be talked into requesting
``../../.ssh/id_rsa`` by a booby-trapped source file, and prompt injection cannot be
ruled out. Containment is therefore enforced here, in code, not in the prompt.
"""

from __future__ import annotations

import codecs
from pathlib import Path

from ..errors import SandboxViolation

#: How many leading bytes are inspected for binary content.
_BINARY_SNIFF_BYTES = 8192

#: Byte-order marks, longest first. Order matters: the UTF-32-LE BOM begins with
#: the UTF-16-LE BOM's bytes, so checking UTF-16 first would misread every UTF-32
#: file.
_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

#: Tried in order. `utf-8-sig` strips a BOM; `gbk` covers Chinese Windows files;
#: `latin-1` never fails, so it is the guaranteed terminator.
_ENCODINGS = ("utf-8", "utf-8-sig", "gbk", "latin-1")


def bom_encoding(data: bytes) -> str | None:
    """Return the encoding named by a byte-order mark, if there is one."""
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return encoding
    return None


def resolve_in_root(root: Path | str, user_path: str) -> Path:
    """Resolve *user_path* against *root*, refusing anything that escapes it.

    ``.resolve()`` is what makes this safe: it canonicalises ``..`` segments *and*
    follows symlinks, so a link inside the repo pointing at ``C:/Windows``
    resolves outside the root and is rejected by the same check as a plain
    ``../`` escape.

    Note the comparison uses :meth:`Path.is_relative_to`, not ``str.startswith``.
    A prefix check would accept ``C:/repo-evil`` for a root of ``C:/repo``.

    Raises:
        SandboxViolation: on NUL bytes, or any path resolving outside *root*.
    """
    if not user_path or "\x00" in user_path:
        raise SandboxViolation(f"rejected path (empty or contains NUL byte): {user_path!r}")

    root_resolved = Path(root).resolve()
    raw = Path(user_path)

    # Absolute paths are allowed only if they still land inside the root; this
    # keeps `read_file("/abs/path/inside/repo")` working while blocking the rest.
    candidate = raw.resolve() if raw.is_absolute() else (root_resolved / raw).resolve()

    if candidate != root_resolved and not candidate.is_relative_to(root_resolved):
        raise SandboxViolation(f"path escapes repository root: {user_path!r}")

    return candidate


def looks_binary(data: bytes) -> bool:
    """True if *data* looks like a binary blob rather than text.

    A NUL byte in the first chunk is the cheap heuristic git and grep use. It
    produces false positives on UTF-16/UTF-32, whose ASCII range is half NUL
    bytes, so a recognised BOM is checked first and short-circuits to "text".
    """
    if bom_encoding(data) is not None:
        return False
    return b"\x00" in data[:_BINARY_SNIFF_BYTES]


def decode_text(data: bytes) -> tuple[str, str]:
    """Decode *data*, falling back through a chain of encodings.

    Returns:
        ``(text, encoding_used)``. The encoding is reported in the run output so
        a surprising decode is visible rather than silent.

    A file that decodes under no encoding still returns text -- decoded as
    latin-1 -- because a mojibake review is more useful than a crashed one.
    ``errors="replace"`` handles the case where even the declared encoding is
    damaged partway through the file.
    """
    # An explicit BOM is authoritative: trust it over guesswork.
    declared = bom_encoding(data)
    if declared is not None:
        try:
            return data.decode(declared), declared
        except (UnicodeDecodeError, LookupError):
            pass  # malformed despite the BOM; fall through to the chain

    for enc in _ENCODINGS:
        try:
            return data.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    # Unreachable in practice: latin-1 maps all 256 byte values.
    return data.decode("latin-1", errors="replace"), "latin-1/replace"


def read_text_safely(
    path: Path,
    *,
    max_bytes: int,
    root: Path | str,
) -> tuple[str, str, bool]:
    """Read *path* as text with sandboxing, a size cap, and encoding fallback.

    Args:
        path: Path to read. Re-checked against *root* even though callers
            normally resolve first -- defence in depth, since this is the last
            function before bytes hit the disk.
        max_bytes: Files larger than this are truncated, not skipped, so a big
            file still gets a partial review.
        root: Repository root to confine reads to.

    Returns:
        ``(text, encoding_used, was_truncated)``.

    Raises:
        SandboxViolation: path escapes *root*.
        ToolError: the file is binary, missing, or unreadable.
    """
    from ..errors import ToolError

    resolved = resolve_in_root(root, str(path))

    if not resolved.exists():
        raise ToolError(f"no such file: {resolved}")
    if not resolved.is_file():
        raise ToolError(f"not a regular file: {resolved}")

    try:
        with resolved.open("rb") as fh:
            data = fh.read(max_bytes + 1)
    except OSError as exc:
        raise ToolError(f"could not read {resolved}: {exc}") from exc

    was_truncated = len(data) > max_bytes
    data = data[:max_bytes]

    if looks_binary(data):
        raise ToolError(f"{resolved} looks binary; skipped")

    text, encoding = decode_text(data)
    return text, encoding, was_truncated
