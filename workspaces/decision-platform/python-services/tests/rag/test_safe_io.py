from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from app.rag.safe_io import RagSafeIoError, list_approved_regular_files
from app.rag.safe_io import read_approved_regular_file
from app.rag.safe_io import write_approved_generated_file, write_approved_new_file


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
    assert len(result.content_sha256) == 64
    assert result.inode > 0


def test_read_approved_regular_file_rejects_path_escape_and_oversize(tmp_path: Path) -> None:
    root = tmp_path / "approved"
    root.mkdir()
    (root / "safe.md").write_bytes(b"x" * 8)

    with pytest.raises(RagSafeIoError):
        read_approved_regular_file(approved_root=root, relative_path="../safe.md", max_bytes=64)
    with pytest.raises(RagSafeIoError):
        read_approved_regular_file(approved_root=root, relative_path="./safe.md", max_bytes=64)
    with pytest.raises(RagSafeIoError):
        read_approved_regular_file(
            approved_root=root,
            relative_path="cards/./safe.md",
            max_bytes=64,
        )
    with pytest.raises(RagSafeIoError):
        read_approved_regular_file(approved_root=root, relative_path="/safe.md", max_bytes=64)
    with pytest.raises(RagSafeIoError):
        read_approved_regular_file(approved_root=root, relative_path="safe.md", max_bytes=4)
    with pytest.raises(RagSafeIoError, match="absolute filesystem path"):
        read_approved_regular_file(
            approved_root=Path("relative/approved"),
            relative_path="safe.md",
            max_bytes=64,
        )
    with pytest.raises(RagSafeIoError, match="absolute filesystem path"):
        read_approved_regular_file(
            approved_root=Path("~/approved"),
            relative_path="safe.md",
            max_bytes=64,
        )


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


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO support is required")
def test_read_approved_regular_file_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    root = tmp_path / "approved"
    root.mkdir()
    os.mkfifo(root / "blocking.md")
    python_services_root = Path(__file__).resolve().parents[2]
    script = """
from pathlib import Path
import sys

from app.rag.safe_io import RagSafeIoError, read_approved_regular_file

try:
    read_approved_regular_file(
        approved_root=Path(sys.argv[1]),
        relative_path="blocking.md",
        max_bytes=64,
    )
except RagSafeIoError:
    print("rejected")
    raise SystemExit(0)
raise SystemExit(1)
"""

    # 별도 process timeout으로 취약한 blocking open도 전체 test suite를 멈추지 못하게 한다.
    result = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        cwd=python_services_root,
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "rejected"


def test_read_approved_regular_file_rejects_shared_write_and_wrong_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "approved"
    cards = root / "cards"
    cards.mkdir(parents=True)
    document = cards / "safe.md"
    document.write_bytes(b"safe")

    os.chmod(document, 0o664)
    with pytest.raises(RagSafeIoError, match="group/other writable"):
        read_approved_regular_file(
            approved_root=root,
            relative_path="cards/safe.md",
            max_bytes=64,
        )

    os.chmod(document, 0o644)
    os.chmod(cards, 0o775)
    with pytest.raises(RagSafeIoError, match="group/other writable"):
        read_approved_regular_file(
            approved_root=root,
            relative_path="cards/safe.md",
            max_bytes=64,
        )

    os.chmod(cards, 0o755)
    current_owner = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: current_owner + 1)
    with pytest.raises(RagSafeIoError, match="owned by the current process user"):
        read_approved_regular_file(
            approved_root=root,
            relative_path="cards/safe.md",
            max_bytes=64,
        )


def test_read_approved_regular_file_rejects_hardlink_and_mid_read_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "approved"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"outside")
    os.link(outside, root / "hardlink.md")
    with pytest.raises(RagSafeIoError):
        read_approved_regular_file(
            approved_root=root,
            relative_path="hardlink.md",
            max_bytes=64,
        )

    racing = root / "racing.md"
    racing.write_bytes(b"stable-before-read")
    original_read = os.read
    changed = False

    def racing_read(file_descriptor: int, size: int) -> bytes:
        nonlocal changed
        payload = original_read(file_descriptor, size)
        if not changed:
            changed = True
            racing.write_bytes(b"changed-during-read")
        return payload

    monkeypatch.setattr(os, "read", racing_read)
    with pytest.raises(RagSafeIoError):
        read_approved_regular_file(
            approved_root=root,
            relative_path="racing.md",
            max_bytes=64,
        )


def test_write_approved_new_file_is_mode_0600_durable_and_no_overwrite() -> None:
    with tempfile.TemporaryDirectory(prefix="rag-safe-io-", dir="/tmp") as temporary:
        root = Path(temporary) / "approved"
        (root / "cards").mkdir(parents=True)
        payload = b"# Source Card: synthetic\n"

        result = write_approved_new_file(
            approved_root=root,
            relative_path="cards/synthetic.md",
            content=payload,
            max_bytes=1024,
        )

        target = root / "cards" / "synthetic.md"
        assert result.absolute_path == target
        assert result.bytes_written == len(payload)
        assert target.read_bytes() == payload
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert list((root / "cards").glob(".*.tmp")) == []

        with pytest.raises(RagSafeIoError):
            write_approved_new_file(
                approved_root=root,
                relative_path="cards/synthetic.md",
                content=b"replacement",
                max_bytes=1024,
            )
        assert target.read_bytes() == payload


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support is required")
def test_write_approved_new_file_rejects_symlink_parent_and_target(tmp_path: Path) -> None:
    root = tmp_path / "approved"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, root / "linked-parent")
    with pytest.raises(RagSafeIoError):
        write_approved_new_file(
            approved_root=root,
            relative_path="linked-parent/card.md",
            content=b"no escape",
            max_bytes=64,
        )

    (root / "cards").mkdir()
    outside_target = outside / "existing.md"
    outside_target.write_bytes(b"outside")
    os.symlink(outside_target, root / "cards" / "linked.md")
    with pytest.raises(RagSafeIoError):
        write_approved_new_file(
            approved_root=root,
            relative_path="cards/linked.md",
            content=b"no overwrite",
            max_bytes=64,
        )
    assert outside_target.read_bytes() == b"outside"


def test_write_approved_new_file_rejects_unsafe_parent_mode_and_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "approved"
    cards = root / "cards"
    cards.mkdir(parents=True)

    os.chmod(cards, 0o775)
    with pytest.raises(RagSafeIoError, match="group/other writable"):
        write_approved_new_file(
            approved_root=root,
            relative_path="cards/mode.md",
            content=b"safe",
            max_bytes=64,
        )

    os.chmod(cards, 0o755)
    current_owner = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: current_owner + 1)
    with pytest.raises(RagSafeIoError, match="owned by the current process user"):
        write_approved_new_file(
            approved_root=root,
            relative_path="cards/owner.md",
            content=b"safe",
            max_bytes=64,
        )


def test_write_approved_new_file_publishes_only_the_open_anonymous_inode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory(prefix="rag-safe-io-race-", dir="/tmp") as temporary:
        root = Path(temporary) / "approved"
        cards = root / "cards"
        cards.mkdir(parents=True)
        original_link = os.link

        def injecting_link(
            source: str,
            target: str,
            *,
            src_dir_fd: int | None = None,
            dst_dir_fd: int | None = None,
            follow_symlinks: bool = True,
        ) -> None:
            assert source.startswith("/proc/self/fd/")
            (cards / ".attacker.tmp").write_bytes(b"attacker replacement")
            original_link(
                source,
                target,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
                follow_symlinks=follow_symlinks,
            )

        monkeypatch.setattr(os, "link", injecting_link)

        result = write_approved_new_file(
            approved_root=root,
            relative_path="cards/synthetic.md",
            content=b"verified content",
            max_bytes=1024,
        )

        assert result.bytes_written == len(b"verified content")
        assert (cards / "synthetic.md").read_bytes() == b"verified content"
        assert any(path.read_bytes() == b"attacker replacement" for path in cards.glob(".*.tmp"))


def test_write_approved_new_file_does_not_delete_target_swapped_after_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory(prefix="rag-safe-io-target-race-", dir="/tmp") as temporary:
        root = Path(temporary) / "approved"
        cards = root / "cards"
        cards.mkdir(parents=True)
        target = cards / "synthetic.md"
        original_open = os.open
        target_swapped = False

        def swapping_open(
            path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            nonlocal target_swapped
            if path == "synthetic.md" and dir_fd is not None and not target_swapped:
                target_swapped = True
                target.unlink()
                target.write_bytes(b"unrelated replacement")
                os.chmod(target, 0o600)
            return original_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", swapping_open)

        with pytest.raises(RagSafeIoError, match="published inode mismatched"):
            write_approved_new_file(
                approved_root=root,
                relative_path="cards/synthetic.md",
                content=b"verified content",
                max_bytes=1024,
            )
        assert target.read_bytes() == b"unrelated replacement"


def test_write_approved_generated_file_replaces_only_safe_regular_leaf_and_lists_bytes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "approved"
    cards = root / "cards"
    cards.mkdir(parents=True)
    target = cards / "card.md"
    target.write_bytes(b"old card")

    result = write_approved_generated_file(
        approved_root=root,
        relative_path="cards/card.md",
        content=b"new deterministic card\n",
        max_bytes=1024,
    )

    assert result.absolute_path == target
    assert target.read_bytes() == b"new deterministic card\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert list_approved_regular_files(
        approved_root=root,
        relative_directory="cards",
        max_entries=4,
        max_bytes=1024,
    ) == {"card.md": b"new deterministic card\n"}


@pytest.mark.parametrize("unsafe_kind", ("symlink", "directory", "hardlink"))
def test_write_approved_generated_file_rejects_unsafe_existing_leaf(
    unsafe_kind: str,
    tmp_path: Path,
) -> None:
    root = tmp_path / "approved"
    cards = root / "cards"
    cards.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"outside sentinel")
    target = cards / "card.md"
    if unsafe_kind == "symlink":
        os.symlink(outside, target)
    elif unsafe_kind == "directory":
        target.mkdir()
    else:
        os.link(outside, target)

    with pytest.raises(RagSafeIoError):
        write_approved_generated_file(
            approved_root=root,
            relative_path="cards/card.md",
            content=b"replacement must not escape",
            max_bytes=1024,
        )

    assert outside.read_bytes() == b"outside sentinel"
