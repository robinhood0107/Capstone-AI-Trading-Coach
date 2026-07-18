"""한 pinned Python environment 안에서 해당 track의 frozen reference만 실행한다."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from oracle_common import (
    OracleContractError,
    atomic_write_json,
    normalize_json_value,
    require_lower_sha256,
    require_safe_basename,
    resolve_within,
    strict_json_load,
)

PRODUCTION_FUNCTIONS = frozenset(
    {
        "simple_returns",
        "log_returns",
        "cumulative_return",
        "cagr",
        "realized_volatility",
        "annualized_volatility",
        "max_drawdown",
        "sharpe_ratio",
        "sortino_ratio",
        "historical_var",
        "historical_cvar",
    }
)
RESEARCH_FUNCTIONS = frozenset(
    {
        "historical_expected_shortfall",
        "realized_variance",
        "realized_volatility_intraday",
        "lo_adjusted_sharpe_ratio",
        "probabilistic_sharpe_ratio",
        "deflated_sharpe_ratio",
        "kupiec_unconditional_coverage_test",
        "christoffersen_independence_test",
        "christoffersen_conditional_coverage_test",
    }
)
PRODUCTION_ERROR_CODES = frozenset(
    {
        "input_type_invalid",
        "input_shape_invalid",
        "input_empty",
        "input_too_short",
        "input_too_long",
        "input_bool_invalid",
        "input_complex_invalid",
        "input_non_finite",
        "prices_non_positive",
        "equity_initial_non_positive",
        "equity_negative",
        "simple_return_below_minus_one",
        "periods_per_year_invalid",
        "risk_free_rate_invalid",
        "target_return_invalid",
        "confidence_invalid",
        "denominator_zero",
        "tail_empty",
        "result_non_finite",
    }
)


def _validate_binary_manifest(manifest: Mapping[str, Any]) -> tuple[int, int, str, str]:
    required_fields = {
        "schemaVersion",
        "fixtureId",
        "argumentName",
        "fileName",
        "encoding",
        "dtype",
        "byteOrder",
        "arrayOrder",
        "shape",
        "count",
        "byteLength",
        "sha256",
        "generator",
    }
    allowed_fields = required_fields | {"expectedSemanticError"}
    if not required_fields <= manifest.keys() or not manifest.keys() <= allowed_fields:
        raise OracleContractError("binary manifest has unknown or missing fields")
    constants = {
        "schemaVersion": "s1.4x-binary-array-v1",
        "encoding": "ieee754-binary64",
        "dtype": "float64",
        "byteOrder": "little",
        "arrayOrder": "C",
    }
    for field, expected in constants.items():
        if manifest.get(field) != expected:
            raise OracleContractError(f"binary manifest {field} mismatch")
    shape = manifest.get("shape")
    if (
        not isinstance(shape, list)
        or len(shape) != 1
        or type(shape[0]) is not int
        or shape[0] <= 0
    ):
        raise OracleContractError("binary manifest shape must be rank-one")
    count = manifest.get("count")
    byte_length = manifest.get("byteLength")
    if type(count) is not int or count != shape[0]:
        raise OracleContractError("binary manifest count mismatch")
    if type(byte_length) is not int or byte_length != count * 8:
        raise OracleContractError("binary manifest byteLength mismatch")
    if byte_length > 536_870_912:
        raise OracleContractError("binary manifest exceeds allocation cap")
    file_name = require_safe_basename(manifest.get("fileName"))
    expected_sha = require_lower_sha256(manifest.get("sha256"), field="sha256")
    return count, byte_length, file_name, expected_sha


def _load_binary_reference(descriptor: Mapping[str, Any], fixture_root: Path) -> Any:
    if set(descriptor) != {"kind", "manifestFile"}:
        raise OracleContractError("binaryFloat64 descriptor has unknown or missing fields")
    manifest_file = require_safe_basename(descriptor.get("manifestFile"), field="manifestFile")
    large_root = resolve_within(fixture_root, "large", must_exist=True)
    manifest_path = resolve_within(large_root, manifest_file, must_exist=True)
    loaded_manifest = strict_json_load(manifest_path)
    if not isinstance(loaded_manifest, dict):
        raise OracleContractError("binary manifest must be an object")
    count, byte_length, file_name, expected_sha = _validate_binary_manifest(loaded_manifest)
    generated_root = resolve_within(large_root, "generated", must_exist=True)
    binary_path = resolve_within(generated_root, file_name, must_exist=True)
    try:
        payload = binary_path.read_bytes()
    except OSError as exc:
        raise OracleContractError("unable to read generated binary fixture") from exc
    if len(payload) != byte_length:
        raise OracleContractError("generated binary fixture size mismatch")
    if hashlib.sha256(payload).hexdigest() != expected_sha:
        raise OracleContractError("generated binary fixture SHA-256 mismatch")
    try:
        import numpy as np
    except ImportError as exc:
        raise OracleContractError("pinned reference environment has no NumPy") from exc
    values = np.frombuffer(payload, dtype="<f8", count=count)
    if values.size != count:
        raise OracleContractError("generated binary fixture decode count mismatch")
    return values.copy(order="C")


def _decode_argument(value: Any, fixture_root: Path) -> Any:
    if isinstance(value, dict):
        if value.get("kind") == "binaryFloat64":
            return _load_binary_reference(value, fixture_root)
        return {key: _decode_argument(item, fixture_root) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_argument(item, fixture_root) for item in value]
    return value


def _prepare_research_provenance(arguments: dict[str, Any], model_type: type[Any]) -> None:
    provenance = arguments.get("trial_provenance")
    if not isinstance(provenance, dict):
        return
    try:
        arguments["trial_provenance"] = model_type(**provenance)
    except TypeError:
        # Reference validator owns the stable error for malformed object contents.
        arguments["trial_provenance"] = provenance


def _load_track(
    track: str,
    reference_root: Path,
) -> tuple[dict[str, Callable[..., Any]], type[BaseException], frozenset[str], type[Any] | None]:
    if track == "s1.4":
        sys.path.insert(0, str(reference_root))
        module = importlib.import_module("app.financial_engineering")
        functions = {
            name: getattr(module, name)
            for name in sorted(PRODUCTION_FUNCTIONS)
        }
        return functions, ValueError, PRODUCTION_ERROR_CODES, None
    if track == "s1.4r":
        sys.path.insert(0, str(reference_root / "src"))
        module = importlib.import_module("s1_4r_risk_research.numpy_reference")
        errors = importlib.import_module("s1_4r_risk_research.errors")
        models = importlib.import_module("s1_4r_risk_research.models")
        functions = {
            name: getattr(module, name)
            for name in sorted(RESEARCH_FUNCTIONS)
        }
        return (
            functions,
            errors.ResearchValidationError,
            errors.RESEARCH_ERROR_CODES,
            models.EffectiveTrialProvenance,
        )
    raise OracleContractError(f"unknown reference track: {track}")


def _execute_case(
    case: Mapping[str, Any],
    *,
    functions: Mapping[str, Callable[..., Any]],
    stable_error_type: type[BaseException],
    stable_error_codes: frozenset[str],
    provenance_type: type[Any] | None,
    fixture_root: Path,
) -> dict[str, Any]:
    function_id = case.get("functionId")
    fixture_id = case.get("fixtureId")
    if not isinstance(function_id, str) or function_id not in functions:
        raise OracleContractError("worker request contains a function outside its track")
    if not isinstance(fixture_id, str) or not fixture_id:
        raise OracleContractError("worker request contains an invalid fixtureId")
    raw_arguments = case.get("arguments")
    if not isinstance(raw_arguments, dict):
        raise OracleContractError("worker case arguments must be an object")
    arguments = {
        key: _decode_argument(value, fixture_root)
        for key, value in raw_arguments.items()
    }
    if function_id == "deflated_sharpe_ratio" and provenance_type is not None:
        _prepare_research_provenance(arguments, provenance_type)
    try:
        values = functions[function_id](**arguments)
    except stable_error_type as exc:
        code = getattr(exc, "code", str(exc))
        if code not in stable_error_codes:
            raise OracleContractError(
                f"reference raised a non-contract error for {function_id}"
            ) from exc
        return {
            "schemaVersion": "s1.4x-result-v1",
            "functionId": function_id,
            "fixtureId": fixture_id,
            "status": "error",
            "errorCode": code,
        }
    return {
        "schemaVersion": "s1.4x-result-v1",
        "functionId": function_id,
        "fixtureId": fixture_id,
        "status": "ok",
        "values": normalize_json_value(values),
    }


def execute_request(
    request: Mapping[str, Any],
    *,
    track: str,
    reference_root: Path,
    fixture_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """한 track request를 실행하고 result batch와 runtime identity를 반환한다."""

    if request.get("schemaVersion") != "s1.4x-request-v1":
        raise OracleContractError("worker request schemaVersion mismatch")
    request_id = request.get("requestId")
    cases = request.get("cases")
    if not isinstance(request_id, str) or not isinstance(cases, list):
        raise OracleContractError("worker request envelope is invalid")
    functions, error_type, error_codes, provenance_type = _load_track(track, reference_root)
    results = [
        _execute_case(
            case,
            functions=functions,
            stable_error_type=error_type,
            stable_error_codes=error_codes,
            provenance_type=provenance_type,
            fixture_root=fixture_root,
        )
        for case in cases
        if isinstance(case, dict)
    ]
    if len(results) != len(cases):
        raise OracleContractError("worker request contains a non-object case")
    try:
        import numpy as np
    except ImportError as exc:
        raise OracleContractError("pinned reference environment has no NumPy") from exc
    batch = {
        "schemaVersion": "s1.4x-result-batch-v1",
        "requestId": request_id,
        "implementation": f"python-numpy-{track}-reference",
        "results": results,
    }
    runtime = {
        "track": track,
        "pythonImplementation": sys.implementation.name,
        "pythonVersion": ".".join(str(part) for part in sys.version_info[:3]),
        "numpyVersion": np.__version__,
    }
    return batch, runtime


def _parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Execute one S1.4 or S1.4R reference request inside its pinned process."
    )
    parser.add_argument("--track", required=True, choices=("s1.4", "s1.4r"))
    parser.add_argument("--reference-root", required=True, type=Path)
    parser.add_argument("--fixture-root", required=True, type=Path)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--runtime-output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Worker CLI는 stdout/stderr를 비워 두고 atomic result files만 기록한다."""

    arguments = _parse_arguments(argv)
    request = strict_json_load(arguments.request)
    if not isinstance(request, dict):
        raise OracleContractError("worker request must be an object")
    batch, runtime = execute_request(
        request,
        track=arguments.track,
        reference_root=arguments.reference_root.resolve(),
        fixture_root=arguments.fixture_root.resolve(),
    )
    atomic_write_json(arguments.output, batch)
    atomic_write_json(arguments.runtime_output, runtime)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OracleContractError as error:
        # Worker 오류는 parent가 exit와 sanitized leaf만 수집하며 stack trace를 내보내지 않는다.
        print(f"reference_worker_failed:{error}", file=sys.stderr)
        raise SystemExit(1) from None
