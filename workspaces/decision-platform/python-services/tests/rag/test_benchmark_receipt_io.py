from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.rag.benchmark_receipt_io import (
    BenchmarkReceiptIoError,
    write_benchmark_receipt,
)


def test_benchmark_receipt_write_is_atomic_and_repeatable(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)

    first = write_benchmark_receipt(
        approved_root=root,
        relative_directory="PADDLE_VL/CPU",
        filename="fixture.json",
        payload=b'{"attempt":1}\n',
    )
    second = write_benchmark_receipt(
        approved_root=root,
        relative_directory="PADDLE_VL/CPU",
        filename="fixture.json",
        payload=b'{"attempt":2}\n',
    )

    assert first.bytes_written == 14
    assert second.bytes_written == 14
    assert (root / "PADDLE_VL/CPU/fixture.json").read_bytes() == b'{"attempt":2}\n'
    assert not list(root.rglob("*.tmp-*"))


@pytest.mark.parametrize("unsafe_leaf", ["SYMLINK", "HARDLINK", "DIRECTORY"])
def test_benchmark_receipt_rejects_unsafe_existing_leaf_without_touching_sentinel(
    tmp_path: Path,
    unsafe_leaf: str,
) -> None:
    root = tmp_path / "runtime"
    output = root / "PADDLE_STRUCTURED/CPU"
    output.mkdir(parents=True, mode=0o700)
    sentinel = tmp_path / "outside.json"
    sentinel.write_bytes(b"outside-sentinel")
    target = output / "fixture.json"
    if unsafe_leaf == "SYMLINK":
        target.symlink_to(sentinel)
    elif unsafe_leaf == "HARDLINK":
        os.link(sentinel, target)
    else:
        target.mkdir()

    with pytest.raises(BenchmarkReceiptIoError, match="OCR_BENCHMARK_OUTPUT_UNSAFE"):
        write_benchmark_receipt(
            approved_root=root,
            relative_directory="PADDLE_STRUCTURED/CPU",
            filename="fixture.json",
            payload=b'{"attempt":1}\n',
        )

    assert sentinel.read_bytes() == b"outside-sentinel"


def test_benchmark_receipt_rejects_symlink_root_and_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    root_link = tmp_path / "runtime-link"
    root_link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(BenchmarkReceiptIoError, match="OCR_BENCHMARK_OUTPUT_UNSAFE"):
        write_benchmark_receipt(
            approved_root=root_link,
            relative_directory="PADDLE_VL/CPU",
            filename="fixture.json",
            payload=b"{}\n",
        )

    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    (root / "PADDLE_VL").symlink_to(outside, target_is_directory=True)
    with pytest.raises(BenchmarkReceiptIoError, match="OCR_BENCHMARK_OUTPUT_UNSAFE"):
        write_benchmark_receipt(
            approved_root=root,
            relative_directory="PADDLE_VL/CPU",
            filename="fixture.json",
            payload=b"{}\n",
        )

    assert list(outside.iterdir()) == []


@pytest.mark.parametrize(
    ("relative_directory", "filename"),
    [
        ("../outside", "fixture.json"),
        ("PADDLE_VL/CPU", "../fixture.json"),
        ("PADDLE_VL/CPU", "fixture.txt"),
    ],
)
def test_benchmark_receipt_rejects_path_escape_and_unexpected_filename(
    tmp_path: Path,
    relative_directory: str,
    filename: str,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)

    with pytest.raises(BenchmarkReceiptIoError, match="OCR_BENCHMARK_OUTPUT_INVALID"):
        write_benchmark_receipt(
            approved_root=root,
            relative_directory=relative_directory,
            filename=filename,
            payload=b"{}\n",
        )
