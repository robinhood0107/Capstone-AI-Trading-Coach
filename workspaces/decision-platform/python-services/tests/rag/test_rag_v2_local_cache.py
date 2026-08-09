from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.rag.rag_v2_local_cache import (
    RagV2LocalCacheError,
    clean_local_rag_cache,
)


def test_cache_clean_removes_only_fixed_local_cache_directories(tmp_path: Path) -> None:
    _secure_root(tmp_path)
    cache_file = tmp_path / "cache" / "nested" / "derived.json"
    raw_file = tmp_path / "oa-raw" / "source-001" / "raw.pdf"
    protected = tmp_path / "control" / "owner-import.json"
    cache_file.parent.mkdir(parents=True, mode=0o700)
    raw_file.parent.mkdir(parents=True, mode=0o700)
    protected.parent.mkdir(parents=True, mode=0o700)
    cache_file.write_text("derived", encoding="utf-8")
    raw_file.write_bytes(b"%PDF-1.7\nlocal raw only")
    protected.write_text("private-control", encoding="utf-8")
    _secure_tree(tmp_path)

    receipt = clean_local_rag_cache(local_root=tmp_path)

    assert receipt.removed_entries == 6
    assert not (tmp_path / "cache").exists()
    assert not (tmp_path / "oa-raw").exists()
    assert protected.read_text(encoding="utf-8") == "private-control"


def test_cache_clean_rejects_symlink_or_shared_writable_tree_before_deletion(tmp_path: Path) -> None:
    _secure_root(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir(mode=0o700)
    (cache / "safe.txt").write_text("safe", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("must survive", encoding="utf-8")
    os.symlink(outside, cache / "linked.txt")
    _secure_tree(tmp_path)

    with pytest.raises(RagV2LocalCacheError, match="LOCAL_CACHE_UNSAFE"):
        clean_local_rag_cache(local_root=tmp_path)

    assert (cache / "safe.txt").is_file()
    assert outside.read_text(encoding="utf-8") == "must survive"


def test_cache_clean_is_idempotent_when_no_managed_cache_exists(tmp_path: Path) -> None:
    _secure_root(tmp_path)

    receipt = clean_local_rag_cache(local_root=tmp_path)

    assert receipt.removed_entries == 0


def _secure_root(root: Path) -> None:
    os.chmod(root, 0o700)


def _secure_tree(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if not path.is_symlink():
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
