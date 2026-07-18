"""Frozen oracle·candidate 결과를 typed tolerance와 exact field 규칙으로 비교한다."""

from __future__ import annotations

import argparse
import itertools
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped]
from referencing.exceptions import Unresolvable

from oracle_common import (
    OracleContractError,
    atomic_write_json,
    canonical_json_bytes,
    strict_json_load,
)

TOLERANCES: dict[str, tuple[float, float]] = {
    "handPaper": (1.0e-12, 1.0e-12),
    "largeProperty": (1.0e-10, 1.0e-12),
}
CANONICAL_RESULT_SCHEMA = (
    Path(__file__).resolve().parent.parent
    / "contract"
    / "schemas"
    / "canonical-result.schema.json"
)


def _contains_binary_reference(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("kind") == "binaryFloat64":
            return True
        return any(_contains_binary_reference(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_binary_reference(item) for item in value)
    return False


def _case_tolerance_map(request: Any | None) -> dict[str, str]:
    if not isinstance(request, dict):
        return {}
    cases = request.get("cases")
    if not isinstance(cases, list):
        return {}
    mapping: dict[str, str] = {}
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("fixtureId"), str):
            continue
        explicit = case.get("toleranceClass")
        if explicit in TOLERANCES:
            tolerance_class = explicit
        else:
            tolerance_class = (
                "largeProperty"
                if _contains_binary_reference(case.get("arguments"))
                else "handPaper"
            )
        mapping[case["fixtureId"]] = tolerance_class
    return mapping


def _load_tolerance_map(
    *,
    expected_path: Path,
    request_path: Path | None,
    explicit_path: Path | None,
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    inferred_request = (
        expected_path.parent.parent / "small" / "canonical-inputs.v1.json"
    )
    selected_request = request_path or (inferred_request if inferred_request.is_file() else None)
    if selected_request is not None:
        mapping.update(_case_tolerance_map(strict_json_load(selected_request)))
    if explicit_path is not None:
        explicit = strict_json_load(explicit_path)
        if not isinstance(explicit, dict):
            raise OracleContractError("tolerance map must be an object")
        for fixture_id, tolerance_class in explicit.items():
            if not isinstance(fixture_id, str) or tolerance_class not in TOLERANCES:
                raise OracleContractError("tolerance map contains an invalid entry")
            mapping[fixture_id] = tolerance_class
    return mapping


def _require_offline_local_refs(value: Any, *, path: str = "$") -> None:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if reference is not None and (
            not isinstance(reference, str)
            or (reference != "#" and not reference.startswith("#/"))
        ):
            raise OracleContractError(
                f"canonical-result schema has a non-local $ref at {path}"
            )
        for key, item in value.items():
            _require_offline_local_refs(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _require_offline_local_refs(item, path=f"{path}[{index}]")


def _validate_canonical_result_schema(batch: dict[str, Any], *, label: str) -> None:
    """tracked Draft schema와 local `$ref`만으로 batch의 portable wire shape를 검증한다."""

    try:
        schema = strict_json_load(CANONICAL_RESULT_SCHEMA)
    except (OSError, OracleContractError) as exc:
        raise OracleContractError("canonical-result schema is unavailable or invalid") from exc
    if not isinstance(schema, dict):
        raise OracleContractError("canonical-result schema must be an object")
    _require_offline_local_refs(schema)
    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        )
        errors: list[Any] = sorted(
            validator.iter_errors(batch),
            key=lambda error: error.json_path,
        )
    except (SchemaError, Unresolvable) as exc:
        raise OracleContractError(
            "canonical-result schema could not be evaluated offline"
        ) from exc
    if errors:
        first = errors[0]
        raise OracleContractError(
            f"{label} violates canonical-result schema at {first.json_path}: "
            f"{first.message}"
        )


def _validate_batch(batch: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(batch, dict):
        raise OracleContractError(f"{label} result batch must be an object")
    _validate_canonical_result_schema(batch, label=label)
    for field in ("schemaVersion", "requestId", "implementation", "results"):
        if field not in batch:
            raise OracleContractError(f"{label} result batch is missing {field}")
    if batch["schemaVersion"] != "s1.4x-result-batch-v1":
        raise OracleContractError(f"{label} has the wrong result-batch schemaVersion")
    if not isinstance(batch["requestId"], str) or not batch["requestId"]:
        raise OracleContractError(f"{label} requestId must be a non-empty string")
    if not isinstance(batch["implementation"], str) or not batch["implementation"]:
        raise OracleContractError(f"{label} implementation must be a non-empty string")
    if not isinstance(batch["results"], list):
        raise OracleContractError(f"{label} results must be an array")
    seen: set[str] = set()
    for index, result in enumerate(batch["results"]):
        if not isinstance(result, dict):
            raise OracleContractError(f"{label} results[{index}] must be an object")
        for field in ("schemaVersion", "functionId", "fixtureId", "status"):
            if field not in result:
                raise OracleContractError(f"{label} results[{index}] is missing {field}")
        if result["schemaVersion"] != "s1.4x-result-v1":
            raise OracleContractError(f"{label} results[{index}] has wrong schemaVersion")
        fixture_id = result["fixtureId"]
        if not isinstance(fixture_id, str) or not fixture_id or fixture_id in seen:
            raise OracleContractError(f"{label} has invalid or duplicate fixtureId")
        seen.add(fixture_id)
        if result["status"] == "ok":
            if "values" not in result or "errorCode" in result:
                raise OracleContractError(f"{label} ok result shape is invalid")
        elif result["status"] == "error":
            if "errorCode" not in result or "values" in result:
                raise OracleContractError(f"{label} error result shape is invalid")
        else:
            raise OracleContractError(f"{label} result status is invalid")
        _reject_negative_zero(result, path=f"results[{index}]")
    return batch


def _reject_negative_zero(value: Any, *, path: str) -> None:
    if isinstance(value, float):
        if value == 0.0 and math.copysign(1.0, value) < 0.0:
            raise OracleContractError(f"negative zero is not normalized at {path}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_negative_zero(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_negative_zero(item, path=f"{path}.{key}")


def _relative_error(actual: float, expected: float) -> float:
    denominator = max(abs(actual), abs(expected))
    return 0.0 if denominator == 0.0 else abs(actual - expected) / denominator


def _append_mismatch(
    mismatches: list[dict[str, Any]],
    *,
    comparison: str,
    function_id: str,
    fixture_id: str,
    path: str,
    expected: Any,
    actual: Any,
    tolerance_class: str,
    absolute_error: float | None = None,
    relative_error: float | None = None,
) -> None:
    mismatches.append(
        {
            "comparison": comparison,
            "functionId": function_id,
            "fixtureId": fixture_id,
            "path": path,
            "expected": expected,
            "actual": actual,
            "absoluteError": absolute_error,
            "relativeError": relative_error,
            "toleranceClass": tolerance_class,
        }
    )


def _compare_value(
    expected: Any,
    actual: Any,
    *,
    mismatches: list[dict[str, Any]],
    comparison: str,
    function_id: str,
    fixture_id: str,
    path: str,
    tolerance_class: str,
) -> None:
    if isinstance(expected, bool):
        if type(actual) is not bool or actual is not expected:
            _append_mismatch(
                mismatches,
                comparison=comparison,
                function_id=function_id,
                fixture_id=fixture_id,
                path=path,
                expected=expected,
                actual=actual,
                tolerance_class=tolerance_class,
            )
        return
    if type(expected) is int:
        if type(actual) is not int or actual != expected:
            _append_mismatch(
                mismatches,
                comparison=comparison,
                function_id=function_id,
                fixture_id=fixture_id,
                path=path,
                expected=expected,
                actual=actual,
                tolerance_class=tolerance_class,
            )
        return
    if isinstance(expected, float):
        if isinstance(actual, bool) or not isinstance(actual, int | float):
            _append_mismatch(
                mismatches,
                comparison=comparison,
                function_id=function_id,
                fixture_id=fixture_id,
                path=path,
                expected=expected,
                actual=actual,
                tolerance_class=tolerance_class,
            )
            return
        actual_float = float(actual)
        rtol, atol = TOLERANCES[tolerance_class]
        absolute_error = abs(actual_float - expected)
        threshold = max(atol, rtol * max(abs(actual_float), abs(expected)))
        if absolute_error > threshold:
            _append_mismatch(
                mismatches,
                comparison=comparison,
                function_id=function_id,
                fixture_id=fixture_id,
                path=path,
                expected=expected,
                actual=actual,
                absolute_error=absolute_error,
                relative_error=_relative_error(actual_float, expected),
                tolerance_class=tolerance_class,
            )
        return
    if isinstance(expected, str) or expected is None:
        if type(actual) is not type(expected) or actual != expected:
            _append_mismatch(
                mismatches,
                comparison=comparison,
                function_id=function_id,
                fixture_id=fixture_id,
                path=path,
                expected=expected,
                actual=actual,
                tolerance_class=tolerance_class,
            )
        return
    if isinstance(expected, list):
        if not isinstance(actual, list):
            _append_mismatch(
                mismatches,
                comparison=comparison,
                function_id=function_id,
                fixture_id=fixture_id,
                path=path,
                expected=expected,
                actual=actual,
                tolerance_class=tolerance_class,
            )
            return
        if len(expected) != len(actual):
            _append_mismatch(
                mismatches,
                comparison=comparison,
                function_id=function_id,
                fixture_id=fixture_id,
                path=f"{path}.length",
                expected=len(expected),
                actual=len(actual),
                tolerance_class=tolerance_class,
            )
        for index, (expected_item, actual_item) in enumerate(zip(expected, actual, strict=False)):
            _compare_value(
                expected_item,
                actual_item,
                mismatches=mismatches,
                comparison=comparison,
                function_id=function_id,
                fixture_id=fixture_id,
                path=f"{path}[{index}]",
                tolerance_class=tolerance_class,
            )
        return
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            _append_mismatch(
                mismatches,
                comparison=comparison,
                function_id=function_id,
                fixture_id=fixture_id,
                path=path,
                expected=expected,
                actual=actual,
                tolerance_class=tolerance_class,
            )
            return
        expected_keys = set(expected)
        actual_keys = set(actual)
        if expected_keys != actual_keys:
            _append_mismatch(
                mismatches,
                comparison=comparison,
                function_id=function_id,
                fixture_id=fixture_id,
                path=f"{path}.keys",
                expected=sorted(expected_keys),
                actual=sorted(actual_keys),
                tolerance_class=tolerance_class,
            )
        for key in sorted(expected_keys & actual_keys):
            _compare_value(
                expected[key],
                actual[key],
                mismatches=mismatches,
                comparison=comparison,
                function_id=function_id,
                fixture_id=fixture_id,
                path=f"{path}.{key}",
                tolerance_class=tolerance_class,
            )
        return
    raise OracleContractError(f"unsupported expected result type at {path}")


def compare_batches(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    *,
    tolerance_classes: Mapping[str, str],
    comparison: str,
) -> list[dict[str, Any]]:
    """두 batch의 ID/order/field와 모든 typed value를 비교해 mismatch를 전부 반환한다."""

    mismatches: list[dict[str, Any]] = []
    if expected["requestId"] != actual["requestId"]:
        _append_mismatch(
            mismatches,
            comparison=comparison,
            function_id="<batch>",
            fixture_id="<batch>",
            path="requestId",
            expected=expected["requestId"],
            actual=actual["requestId"],
            tolerance_class="handPaper",
        )
    expected_results = expected["results"]
    actual_results = actual["results"]
    expected_ids = [result["fixtureId"] for result in expected_results]
    actual_ids = [result["fixtureId"] for result in actual_results]
    if expected_ids != actual_ids:
        _append_mismatch(
            mismatches,
            comparison=comparison,
            function_id="<batch>",
            fixture_id="<batch>",
            path="results.fixtureIdOrder",
            expected=expected_ids,
            actual=actual_ids,
            tolerance_class="handPaper",
        )
    for expected_result, actual_result in zip(expected_results, actual_results, strict=False):
        fixture_id = expected_result["fixtureId"]
        function_id = expected_result["functionId"]
        tolerance_class = tolerance_classes.get(fixture_id, "handPaper")
        _compare_value(
            expected_result,
            actual_result,
            mismatches=mismatches,
            comparison=comparison,
            function_id=function_id,
            fixture_id=fixture_id,
            path="$",
            tolerance_class=tolerance_class,
        )
    return mismatches


def _parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare one or more candidate result batches with the frozen oracle and "
            "with each other. Numeric tolerance is max(atol, rtol*max(abs(a),abs(e)))."
        )
    )
    parser.add_argument("--expected", required=True, type=Path)
    parser.add_argument("--actual", required=True, action="append", type=Path)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--tolerance-map", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint이며 mismatch 하나라도 있으면 nonzero로 종료한다."""

    arguments = _parse_arguments(argv)
    try:
        expected = _validate_batch(strict_json_load(arguments.expected), label="expected")
        actual_batches = [
            _validate_batch(strict_json_load(path), label=path.name)
            for path in arguments.actual
        ]
        tolerance_classes = _load_tolerance_map(
            expected_path=arguments.expected,
            request_path=arguments.request,
            explicit_path=arguments.tolerance_map,
        )
        mismatches: list[dict[str, Any]] = []
        for actual in actual_batches:
            mismatches.extend(
                compare_batches(
                    expected,
                    actual,
                    tolerance_classes=tolerance_classes,
                    comparison=f"{expected['implementation']}->{actual['implementation']}",
                )
            )
        for left, right in itertools.combinations(actual_batches, 2):
            mismatches.extend(
                compare_batches(
                    left,
                    right,
                    tolerance_classes=tolerance_classes,
                    comparison=f"{left['implementation']}->{right['implementation']}",
                )
            )
        report = {
            "schemaVersion": "s1.4x-comparison-report-v1",
            "requestId": expected["requestId"],
            "implementationCount": len(actual_batches),
            "mismatchCount": len(mismatches),
            "mismatches": mismatches,
            "status": "PASS" if not mismatches else "FAIL",
        }
    except OracleContractError as exc:
        report = {
            "schemaVersion": "s1.4x-comparison-report-v1",
            "requestId": None,
            "implementationCount": len(arguments.actual),
            "mismatchCount": 1,
            "mismatches": [{"path": "$", "error": str(exc)}],
            "status": "FAIL",
        }
    if arguments.output is not None:
        atomic_write_json(arguments.output, report)
    print(canonical_json_bytes(report).decode("utf-8"), end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
