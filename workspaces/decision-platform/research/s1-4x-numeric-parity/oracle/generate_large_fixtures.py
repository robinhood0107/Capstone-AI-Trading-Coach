"""PCG64로 large Float64 LE fixture를 재생성하고 tracked manifest와 대조한다."""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from oracle_common import (
    OracleContractError,
    canonical_json_bytes,
    find_repo_root,
    require_lower_sha256,
    require_safe_basename,
    strict_json_load,
)

ALLOCATION_CAP_BYTES = 536_870_912
EXPECTED_ALGORITHM = "numpy-pcg64"
EXPECTED_NUMPY_VERSION = "numpy-2.5.1"
SUPPORTED_DISTRIBUTIONS = frozenset(
    {"standard_normal", "normal", "uniform", "lognormal"}
)


def _require_exact_keys(
    value: Any,
    *,
    required: set[str],
    allowed: set[str],
    field: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OracleContractError(f"{field} must be an object")
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - allowed)
    if missing or unknown:
        raise OracleContractError(
            f"{field} keys mismatch: missing={missing}, unknown={unknown}"
        )
    return value


def _finite_real(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise OracleContractError(f"{field} must be a real number")
    result = float(value)
    if not math.isfinite(result):
        raise OracleContractError(f"{field} must be finite")
    return result


def _distribution_factory(
    generator: Any,
    name: str,
    parameters: dict[str, Any],
) -> Callable[[int], Any]:
    if name == "standard_normal":
        _require_exact_keys(
            parameters,
            required=set(),
            allowed=set(),
            field="generator.parameters",
        )
        return lambda size: generator.standard_normal(size=size)
    if name == "normal":
        _require_exact_keys(
            parameters,
            required={"loc", "scale"},
            allowed={"loc", "scale"},
            field="generator.parameters",
        )
        loc = _finite_real(parameters["loc"], field="generator.parameters.loc")
        scale = _finite_real(parameters["scale"], field="generator.parameters.scale")
        if scale <= 0.0:
            raise OracleContractError("generator.parameters.scale must be positive")
        return lambda size: generator.normal(loc=loc, scale=scale, size=size)
    if name == "uniform":
        _require_exact_keys(
            parameters,
            required={"low", "high"},
            allowed={"low", "high"},
            field="generator.parameters",
        )
        low = _finite_real(parameters["low"], field="generator.parameters.low")
        high = _finite_real(parameters["high"], field="generator.parameters.high")
        if not low < high:
            raise OracleContractError("generator uniform bounds must satisfy low < high")
        return lambda size: generator.uniform(low=low, high=high, size=size)
    if name == "lognormal":
        _require_exact_keys(
            parameters,
            required={"mean", "sigma"},
            allowed={"mean", "sigma"},
            field="generator.parameters",
        )
        mean = _finite_real(parameters["mean"], field="generator.parameters.mean")
        sigma = _finite_real(parameters["sigma"], field="generator.parameters.sigma")
        if sigma < 0.0:
            raise OracleContractError("generator.parameters.sigma must be non-negative")
        return lambda size: generator.lognormal(mean=mean, sigma=sigma, size=size)
    raise OracleContractError(f"unsupported generator distribution: {name!r}")


def _validate_manifest_shape(manifest: dict[str, Any]) -> tuple[int, int, str, dict[str, Any]]:
    expected_constants: dict[str, Any] = {
        "schemaVersion": "s1.4x-binary-array-v1",
        "encoding": "ieee754-binary64",
        "dtype": "float64",
        "byteOrder": "little",
        "arrayOrder": "C",
    }
    for field, expected in expected_constants.items():
        if manifest.get(field) != expected:
            raise OracleContractError(
                f"{field} mismatch: expected={expected!r}, actual={manifest.get(field)!r}"
            )
    shape = manifest.get("shape")
    if (
        not isinstance(shape, list)
        or len(shape) != 1
        or isinstance(shape[0], bool)
        or not isinstance(shape[0], int)
        or shape[0] < 1
    ):
        raise OracleContractError("shape must be a one-dimensional positive integer array")
    count = manifest.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count != shape[0]:
        raise OracleContractError("count must equal product(shape)")
    byte_length = manifest.get("byteLength")
    if (
        isinstance(byte_length, bool)
        or not isinstance(byte_length, int)
        or byte_length != count * 8
    ):
        raise OracleContractError("byteLength must equal count * 8")
    if byte_length > ALLOCATION_CAP_BYTES:
        raise OracleContractError("binary fixture exceeds the 536870912-byte cap")
    file_name = require_safe_basename(manifest.get("fileName"))
    generator = _require_exact_keys(
        manifest.get("generator"),
        required={
            "algorithm",
            "seed",
            "generatorVersion",
            "distribution",
            "parameters",
            "chunkLength",
        },
        allowed={
            "algorithm",
            "seed",
            "generatorVersion",
            "distribution",
            "parameters",
            "chunkLength",
        },
        field="generator",
    )
    if generator["algorithm"] != EXPECTED_ALGORITHM:
        raise OracleContractError("generator.algorithm must be numpy-pcg64")
    if generator["generatorVersion"] != EXPECTED_NUMPY_VERSION:
        raise OracleContractError("generator.generatorVersion must be numpy-2.5.1")
    seed = generator["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise OracleContractError("generator.seed must be a non-negative exact integer")
    chunk_length = generator["chunkLength"]
    if (
        isinstance(chunk_length, bool)
        or not isinstance(chunk_length, int)
        or not 1 <= chunk_length <= count
    ):
        raise OracleContractError("generator.chunkLength must be in [1, count]")
    distribution = generator["distribution"]
    if distribution not in SUPPORTED_DISTRIBUTIONS:
        raise OracleContractError("generator.distribution is not supported")
    if not isinstance(generator["parameters"], dict):
        raise OracleContractError("generator.parameters must be an object")
    return count, byte_length, file_name, generator


def generate_one(
    manifest_path: Path,
    *,
    generated_dir: Path,
    check: bool,
) -> dict[str, Any]:
    """manifest 하나를 PCG64로 생성하고 raw bytes의 size/hash/finite를 검증한다."""

    try:
        import numpy as np
    except ImportError as exc:
        raise OracleContractError("NumPy 2.5.1 is required to generate fixtures") from exc

    if np.__version__ != "2.5.1":
        raise OracleContractError(
            f"NumPy version mismatch: expected=2.5.1, actual={np.__version__}"
        )
    loaded = strict_json_load(manifest_path)
    if not isinstance(loaded, dict):
        raise OracleContractError("binary manifest must be an object")
    count, expected_length, file_name, specification = _validate_manifest_shape(loaded)
    expected_sha = require_lower_sha256(loaded.get("sha256"), field="sha256")
    bit_generator = np.random.PCG64(specification["seed"])
    random_generator = np.random.Generator(bit_generator)
    draw = _distribution_factory(
        random_generator,
        specification["distribution"],
        specification["parameters"],
    )

    generated_dir.mkdir(parents=True, exist_ok=True)
    output_path = generated_dir / file_name
    descriptor, temporary_name = tempfile.mkstemp(
        dir=generated_dir,
        prefix=f".{file_name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    digest = hashlib.sha256()
    actual_length = 0
    remaining = count
    try:
        with os.fdopen(descriptor, "wb") as stream:
            while remaining:
                current = min(remaining, specification["chunkLength"])
                values = np.asarray(draw(current), dtype="<f8", order="C")
                if values.shape != (current,):
                    raise OracleContractError("generator produced an unexpected shape")
                if not bool(np.all(np.isfinite(values))):
                    raise OracleContractError("success fixture generator produced non-finite input")
                payload = values.tobytes(order="C")
                stream.write(payload)
                digest.update(payload)
                actual_length += len(payload)
                remaining -= current
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, output_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise

    actual_sha = digest.hexdigest()
    status = (
        "PASS"
        if actual_length == expected_length and actual_sha == expected_sha
        else "FAIL"
    )
    result = {
        "manifest": manifest_path.name,
        "fileName": file_name,
        "expectedByteLength": expected_length,
        "actualByteLength": actual_length,
        "expectedSha256": expected_sha,
        "actualSha256": actual_sha,
        "status": status,
    }
    if check and status != "PASS":
        raise OracleContractError(
            f"generated fixture mismatch for {manifest_path.name}: "
            f"expected={expected_length}/{expected_sha}, "
            f"actual={actual_length}/{actual_sha}"
        )
    return result


def _default_s1_4x_root() -> Path:
    return (
        find_repo_root()
        / "workspaces"
        / "decision-platform"
        / "research"
        / "s1-4x-numeric-parity"
    )


def _parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Regenerate tracked large-fixture manifests with NumPy 2.5.1 PCG64. "
            "--check writes ignored raw files then fails on declared size/SHA drift."
        )
    )
    parser.add_argument("--root", type=Path, default=_default_s1_4x_root())
    parser.add_argument("--manifest", action="append", type=Path, default=[])
    parser.add_argument("--generated-dir", type=Path)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint이며 각 manifest 결과를 deterministic JSON으로 stdout에 쓴다."""

    arguments = _parse_arguments(argv)
    root = arguments.root.resolve()
    manifest_root = root / "contract" / "fixtures" / "large"
    manifests = arguments.manifest or sorted(
        manifest_root.glob("*.manifest.json"),
        key=lambda path: path.name.encode(),
    )
    generated_dir = arguments.generated_dir or manifest_root / "generated"
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    for manifest in manifests:
        try:
            results.append(
                generate_one(
                    manifest.resolve(),
                    generated_dir=generated_dir.resolve(),
                    check=arguments.check,
                )
            )
        except OracleContractError as exc:
            failures.append(f"{manifest.name}: {exc}")
    report = {
        "schemaVersion": "s1.4x-large-fixture-generation-result-v1",
        "generator": EXPECTED_ALGORITHM,
        "generatorVersion": EXPECTED_NUMPY_VERSION,
        "manifestCount": len(manifests),
        "results": results,
        "failures": failures,
        "status": "PASS" if not failures else "FAIL",
    }
    print(canonical_json_bytes(report).decode("utf-8"), end="")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
