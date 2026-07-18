from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import pytest

from oracle_common import OracleContractError, atomic_write_json, sha256_bytes
from reference_worker import (
    _execute_case,
    _load_binary_reference,
    _validate_binary_manifest,
)


def _manifest(payload: bytes) -> dict[str, Any]:
    return {
        "schemaVersion": "s1.4x-binary-array-v1",
        "fixtureId": "binary-1",
        "argumentName": "returns",
        "fileName": "binary-1.f64le",
        "encoding": "ieee754-binary64",
        "dtype": "float64",
        "byteOrder": "little",
        "arrayOrder": "C",
        "shape": [2],
        "count": 2,
        "byteLength": 16,
        "sha256": sha256_bytes(payload),
        "generator": {
            "algorithm": "numpy-pcg64",
            "seed": 1,
            "generatorVersion": "numpy-2.5.1",
            "distribution": "normal",
            "parameters": {"loc": 0.0, "scale": 1.0},
            "chunkLength": 2,
        },
    }


def test_worker_binary_loader_checks_exact_fields_size_and_hash(tmp_path: Path) -> None:
    large = tmp_path / "large"
    generated = large / "generated"
    generated.mkdir(parents=True)
    payload = struct.pack("<dd", 1.0, -2.0)
    manifest = _manifest(payload)
    atomic_write_json(large / "binary.manifest.json", manifest)
    (generated / "binary-1.f64le").write_bytes(payload)

    values = _load_binary_reference(
        {"kind": "binaryFloat64", "manifestFile": "binary.manifest.json"},
        tmp_path,
    )

    assert values.tolist() == [1.0, -2.0]

    manifest["sha256"] = "0" * 64
    atomic_write_json(large / "binary.manifest.json", manifest)
    with pytest.raises(OracleContractError, match="SHA-256 mismatch"):
        _load_binary_reference(
            {"kind": "binaryFloat64", "manifestFile": "binary.manifest.json"},
            tmp_path,
        )


def test_worker_manifest_rejects_unknown_fields_and_zero_count() -> None:
    payload = struct.pack("<dd", 1.0, 2.0)
    unknown = _manifest(payload)
    unknown["unknown"] = True
    with pytest.raises(OracleContractError, match="unknown or missing"):
        _validate_binary_manifest(unknown)

    empty = _manifest(payload)
    empty["shape"] = [0]
    empty["count"] = 0
    empty["byteLength"] = 0
    with pytest.raises(OracleContractError, match="rank-one"):
        _validate_binary_manifest(empty)


class StableError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def test_worker_converts_only_allowlisted_stable_errors() -> None:
    case = {
        "fixtureId": "case-1",
        "functionId": "metric",
        "arguments": {},
    }

    def stable() -> float:
        raise StableError("input_invalid")

    result = _execute_case(
        case,
        functions={"metric": stable},
        stable_error_type=StableError,
        stable_error_codes=frozenset({"input_invalid"}),
        provenance_type=None,
        fixture_root=Path("/unused"),
    )
    assert result["status"] == "error"
    assert result["errorCode"] == "input_invalid"

    with pytest.raises(OracleContractError, match="non-contract error"):
        _execute_case(
            case,
            functions={"metric": lambda: (_ for _ in ()).throw(StableError("unknown"))},
            stable_error_type=StableError,
            stable_error_codes=frozenset({"input_invalid"}),
            provenance_type=None,
            fixture_root=Path("/unused"),
        )


def test_worker_recursively_normalizes_success_negative_zero() -> None:
    case = {
        "fixtureId": "case-1",
        "functionId": "metric",
        "arguments": {},
    }

    result = _execute_case(
        case,
        functions={"metric": lambda: {"nested": [-0.0]}},
        stable_error_type=StableError,
        stable_error_codes=frozenset(),
        provenance_type=None,
        fixture_root=Path("/unused"),
    )

    assert result["values"] == {"nested": [0.0]}
