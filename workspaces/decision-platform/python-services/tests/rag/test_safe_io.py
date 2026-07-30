from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.rag.safe_io import RagSafeIoError, read_approved_regular_file


def test_read_approved_regular_file_uses_relative_bounded_regular_files(tmp_path: Path) -> None:
    root = tmp_path / "approved"
    document = root / "cards" / "kis.md"
    document.parent.mkdir(parents=True)
    document.write_bytes(b"# KIS\n\nreference only\n")

    result = read_approved_regular_file(
        approved_root=root,
        relative_path="cards/kis.md",
        max_bytes=1024,
    )

    assert result.relative_path == "cards/kis.md"
    assert result.absolute_path == document
    assert result.content == b"# KIS\n\nreference only\n"


def test_read_approved_regular_file_rejects_path_escape_and_oversize(tmp_path: Path) -> None:
    root = tmp_path / "approved"
    root.mkdir()
    (root / "safe.md").write_bytes(b"x" * 8)

    with pytest.raises(RagSafeIoError):
        read_approved_regular_file(approved_root=root, relative_path="../safe.md", max_bytes=64)
    with pytest.raises(RagSafeIoError):
        read_approved_regular_file(approved_root=root, relative_path="/safe.md", max_bytes=64)
    with pytest.raises(RagSafeIoError):
        read_approved_regular_file(approved_root=root, relative_path="safe.md", max_bytes=4)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support is required")
def test_read_approved_regular_file_rejects_symlink_leaf(tmp_path: Path) -> None:
    root = tmp_path / "approved"
    root.mkdir()
    target = tmp_path / "outside.md"
    target.write_text("outside", encoding="utf-8")
    os.symlink(target, root / "link.md")

    with pytest.raises(RagSafeIoError):
        read_approved_regular_file(approved_root=root, relative_path="link.md", max_bytes=64)


def test_read_approved_regular_file_rejects_directory_leaf(tmp_path: Path) -> None:
    root = tmp_path / "approved"
    (root / "nested").mkdir(parents=True)

    with pytest.raises(RagSafeIoError):
        read_approved_regular_file(approved_root=root, relative_path="nested", max_bytes=64)
