"""Path containment and file-safety guards.

The sandbox is the boundary between the model and the filesystem. These tests
cover the escapes that actually get attempted: `..` traversal, absolute paths
outside the root, symlinks pointing out, and prefix-collision directory names.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codereview.errors import SandboxViolation
from codereview.tools.sandbox import (
    decode_text,
    looks_binary,
    read_text_safely,
    resolve_in_root,
)


def test_resolves_relative_path_inside_root(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x = 1", encoding="utf-8")

    resolved = resolve_in_root(tmp_path, "pkg/mod.py")

    assert resolved == (tmp_path / "pkg" / "mod.py").resolve()


def test_allows_absolute_path_that_is_inside_root(tmp_path: Path) -> None:
    target = tmp_path / "mod.py"
    target.write_text("x = 1", encoding="utf-8")

    assert resolve_in_root(tmp_path, str(target)) == target.resolve()


def test_rejects_parent_traversal(tmp_path: Path) -> None:
    with pytest.raises(SandboxViolation):
        resolve_in_root(tmp_path, "../../../etc/passwd")


def test_rejects_traversal_that_climbs_out_and_back(tmp_path: Path) -> None:
    """`sub/../../outside` must not be accepted just because it starts with `sub`."""
    (tmp_path / "sub").mkdir()
    with pytest.raises(SandboxViolation):
        resolve_in_root(tmp_path, "sub/../../outside.py")


def test_rejects_absolute_path_outside_root(tmp_path: Path) -> None:
    with pytest.raises(SandboxViolation):
        resolve_in_root(tmp_path, str(Path(tmp_path).parent / "elsewhere.py"))


def test_rejects_prefix_collision_directory(tmp_path: Path) -> None:
    """`/repo-evil` must not be accepted as inside `/repo`.

    A `str.startswith` check passes this case; `Path.is_relative_to` does not.
    This is the regression test for that specific bug.
    """
    root = tmp_path / "repo"
    evil = tmp_path / "repo-evil"
    root.mkdir()
    evil.mkdir()
    (evil / "secret.py").write_text("x = 1", encoding="utf-8")

    with pytest.raises(SandboxViolation):
        resolve_in_root(root, str(evil / "secret.py"))


def test_rejects_nul_byte(tmp_path: Path) -> None:
    with pytest.raises(SandboxViolation):
        resolve_in_root(tmp_path, "mod\x00.py")


def test_rejects_empty_path(tmp_path: Path) -> None:
    with pytest.raises(SandboxViolation):
        resolve_in_root(tmp_path, "")


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_rejects_symlink_escaping_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("SECRET = 1", encoding="utf-8")

    root = tmp_path / "repo"
    root.mkdir()
    (root / "link.py").symlink_to(outside / "secret.py")

    with pytest.raises(SandboxViolation):
        resolve_in_root(root, "link.py")


# --- content guards -------------------------------------------------------


def test_detects_binary_by_nul_byte() -> None:
    assert looks_binary(b"\x00\x01\x02binary")


def test_plain_text_is_not_binary() -> None:
    assert not looks_binary(b"def foo():\n    return 1\n")


def test_utf16_with_bom_is_not_binary() -> None:
    """UTF-16 text is half NUL bytes; a naive sniff calls it binary and drops it."""
    data = "def greet():\n    return 'hi'\n".encode("utf-16")

    assert not looks_binary(data)
    assert decode_text(data)[1] == "utf-16"


def test_decodes_gbk() -> None:
    text, encoding = decode_text("# 中文注释\nx = 1\n".encode("gbk"))

    assert "中文注释" in text
    assert encoding == "gbk"


def test_decodes_utf8_with_bom() -> None:
    # encode("utf-8-sig") prepends the BOM; the decoded result must not keep it.
    text, encoding = decode_text("x = 1".encode("utf-8-sig"))

    assert encoding == "utf-8-sig"
    assert text == "x = 1"


def test_undecodable_bytes_do_not_crash() -> None:
    """Losing a review is worse than reviewing a file with replaced characters."""
    text, encoding = decode_text(bytes(range(256)))

    assert isinstance(text, str)
    assert encoding


def test_read_rejects_binary_file(tmp_path: Path) -> None:
    from codereview.errors import ToolError

    target = tmp_path / "blob.py"
    target.write_bytes(b"\x00\x01\x02\x03" * 100)

    with pytest.raises(ToolError, match="binary"):
        read_text_safely(target, max_bytes=10_000, root=tmp_path)


def test_read_truncates_oversized_file(tmp_path: Path) -> None:
    target = tmp_path / "big.py"
    target.write_text("x = 1\n" * 5000, encoding="utf-8")

    text, _encoding, truncated = read_text_safely(target, max_bytes=100, root=tmp_path)

    assert truncated
    assert len(text) <= 100


def test_read_rejects_missing_file(tmp_path: Path) -> None:
    from codereview.errors import ToolError

    with pytest.raises(ToolError, match="no such file"):
        read_text_safely(tmp_path / "nope.py", max_bytes=100, root=tmp_path)
