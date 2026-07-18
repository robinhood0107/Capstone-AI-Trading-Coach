from __future__ import annotations

import math
from pathlib import Path

import pytest

from oracle_common import (
    OracleContractError,
    canonical_file_manifest,
    canonical_json_bytes,
    resolve_within,
    sha256_bytes,
    sorted_relative_files,
    strict_json_loads,
)


@pytest.mark.parametrize(
    "payload",
    [
        '{"a":1,"\\u0061":2}',
        '{"value":NaN}',
        '{"value":Infinity}',
        '{"value":-0}',
        '{"value":"\\ud800"}',
    ],
)
def test_strict_json_rejects_ambiguous_or_nonportable_input(payload: str) -> None:
    with pytest.raises(OracleContractError):
        strict_json_loads(payload)


def test_canonical_json_recursively_normalizes_float_negative_zero() -> None:
    payload = canonical_json_bytes({"z": [-0.0, {"x": -0.0}], "a": 1})

    assert payload == b'{"a":1,"z":[0.0,{"x":0.0}]}\n'
    assert not math.copysign(1.0, strict_json_loads(payload.decode())["z"][0]) < 0.0


def test_resolve_within_rejects_parent_and_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret").write_text("no", encoding="utf-8")
    (root / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OracleContractError):
        resolve_within(root, "../outside/secret", must_exist=True)
    with pytest.raises(OracleContractError):
        resolve_within(root, "link/secret", must_exist=True)


def test_manifest_uses_utf8_byte_order_and_exact_line_format(tmp_path: Path) -> None:
    (tmp_path / "z").write_bytes(b"z")
    (tmp_path / "aa").write_bytes(b"a")
    (tmp_path / "\u00e9").write_bytes(b"e")

    files = sorted_relative_files(tmp_path, ["*"])
    payload, entries = canonical_file_manifest(tmp_path, files)

    expected_names = sorted(["z", "aa", "\u00e9"], key=lambda item: item.encode())
    assert [entry["path"] for entry in entries] == expected_names
    assert payload == b"".join(
        f"{entry['sha256']}  {entry['path']}\n".encode() for entry in entries
    )
    assert len(sha256_bytes(payload)) == 64
