from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from generate_large_fixtures import generate_one
from oracle_common import OracleContractError, atomic_write_json, sha256_bytes


def _manifest(*, chunk_length: int = 5) -> dict[str, Any]:
    generator = np.random.Generator(np.random.PCG64(14002))
    payload = np.asarray(
        generator.normal(loc=0.0002, scale=0.02, size=5),
        dtype="<f8",
    ).tobytes(order="C")
    return {
        "schemaVersion": "s1.4x-binary-array-v1",
        "fixtureId": "tiny-returns",
        "argumentName": "returns",
        "fileName": "tiny-returns.f64le",
        "encoding": "ieee754-binary64",
        "dtype": "float64",
        "byteOrder": "little",
        "arrayOrder": "C",
        "shape": [5],
        "count": 5,
        "byteLength": 40,
        "sha256": sha256_bytes(payload),
        "generator": {
            "algorithm": "numpy-pcg64",
            "seed": 14002,
            "generatorVersion": "numpy-2.5.1",
            "distribution": "normal",
            "parameters": {"loc": 0.0002, "scale": 0.02},
            "chunkLength": chunk_length,
        },
    }


def test_generator_is_deterministic_across_chunk_boundaries(tmp_path: Path) -> None:
    generated = tmp_path / "generated"
    manifest_path = tmp_path / "tiny.manifest.json"
    atomic_write_json(manifest_path, _manifest(chunk_length=5))
    first = generate_one(manifest_path, generated_dir=generated, check=True)
    first_bytes = (generated / "tiny-returns.f64le").read_bytes()

    atomic_write_json(manifest_path, _manifest(chunk_length=2))
    second = generate_one(manifest_path, generated_dir=generated, check=True)

    assert first["status"] == second["status"] == "PASS"
    assert (generated / "tiny-returns.f64le").read_bytes() == first_bytes


def test_generator_check_rejects_declared_hash_drift(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["sha256"] = "0" * 64
    manifest_path = tmp_path / "tiny.manifest.json"
    atomic_write_json(manifest_path, manifest)

    with pytest.raises(OracleContractError, match="generated fixture mismatch"):
        generate_one(manifest_path, generated_dir=tmp_path / "generated", check=True)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("shape",), [1, 5]),
        (("count",), 4),
        (("byteLength",), 32),
        (("fileName",), "../escape.f64le"),
        (("generator", "seed"), True),
        (("generator", "generatorVersion"), "numpy-2.5.0"),
        (("generator", "parameters"), {"loc": 0.0, "scale": 0.0}),
    ],
)
def test_generator_rejects_invalid_manifest(
    tmp_path: Path,
    path: tuple[str, ...],
    value: Any,
) -> None:
    manifest = copy.deepcopy(_manifest())
    target = manifest
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value
    manifest_path = tmp_path / "invalid.manifest.json"
    atomic_write_json(manifest_path, manifest)

    with pytest.raises(OracleContractError):
        generate_one(manifest_path, generated_dir=tmp_path / "generated", check=True)
