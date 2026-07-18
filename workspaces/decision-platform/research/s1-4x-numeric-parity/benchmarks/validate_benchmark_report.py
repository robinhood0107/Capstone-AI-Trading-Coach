#!/usr/bin/env python3
"""S1.4X benchmark plan과 block result를 스키마 및 의미 규칙으로 검증한다."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

from benchmark_contract import (
    ContractError,
    sha256_file,
    strict_json_load,
    validate_block_result_semantics,
    validate_plan_semantics,
)
from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

BENCHMARKS_DIR = Path(__file__).resolve().parent
S1_4X_DIR = BENCHMARKS_DIR.parent
CONTRACT_DIR = S1_4X_DIR / "contract"
DEFAULT_PLAN = BENCHMARKS_DIR / "benchmark-plan.v1.json"
PLAN_SCHEMA = CONTRACT_DIR / "schemas" / "benchmark-plan.schema.json"
BLOCK_SCHEMA = CONTRACT_DIR / "schemas" / "benchmark-block-result.schema.json"


def _schema_errors(instance: Any, schema_path: Path) -> list[str]:
    schema = strict_json_load(schema_path)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [
        f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.path))
    ]


def validate_plan(path: Path = DEFAULT_PLAN, *, verify_files: bool = True) -> dict[str, Any]:
    """계획의 JSON Schema, exact 89-case 계약, 참조 파일 digest를 한 번에 검증한다."""

    plan_object = strict_json_load(path)
    errors = _schema_errors(plan_object, PLAN_SCHEMA)
    if errors:
        raise ContractError("PLAN_SCHEMA_INVALID:\n" + "\n".join(errors))
    validate_plan_semantics(plan_object)
    plan = cast(dict[str, Any], plan_object)
    if verify_files:
        expected_identity = plan["fixtureFreezeIdentity"]
        digest_targets = {
            "referenceLockSha256": CONTRACT_DIR / "reference-lock.v1.json",
            "canonicalInputsSha256": (
                CONTRACT_DIR / "fixtures" / "small" / "canonical-inputs.v1.json"
            ),
            "canonicalResultsSha256": (
                CONTRACT_DIR / "fixtures" / "expected" / "canonical-results.v1.json"
            ),
        }
        for field, target in digest_targets.items():
            if not target.is_file():
                raise ContractError(f"FROZEN_FILE_MISSING:{target}")
            actual = sha256_file(target)
            if expected_identity[field] != actual:
                raise ContractError(f"FROZEN_FILE_DIGEST_MISMATCH:{field}")
        policy_path = CONTRACT_DIR / "scala-source-policy.v1.json"
        if plan["scalaJmhPolicy"]["sourceAnnotationPolicySha256"] != sha256_file(policy_path):
            raise ContractError("SCALA_SOURCE_POLICY_DIGEST_MISMATCH")
    return plan


def validate_block_result(
    report_path: Path,
    *,
    plan_path: Path = DEFAULT_PLAN,
    native_report_path: Path | None = None,
    expected_boundary_id: str | None = None,
    expected_selector_id: str | None = None,
    verify_plan_files: bool = True,
) -> dict[str, Any]:
    """block 산출물과 실제 native bytes를 함께 검증해 self-reported hash를 거부한다."""

    plan = validate_plan(plan_path, verify_files=verify_plan_files)
    report_object = strict_json_load(report_path)
    errors = _schema_errors(report_object, BLOCK_SCHEMA)
    if errors:
        raise ContractError("BLOCK_RESULT_SCHEMA_INVALID:\n" + "\n".join(errors))
    report = cast(dict[str, Any], report_object)
    validate_block_result_semantics(
        report,
        plan,
        expected_boundary_id=expected_boundary_id,
        expected_selector_id=expected_selector_id,
    )
    actual_native_report = native_report_path or report_path.with_name("native.json")
    expected_path_parts = tuple(
        Path(report["block"]["nativeReportPath"]).parts
    )
    if (
        actual_native_report.parent != report_path.parent
        or tuple(actual_native_report.parts[-len(expected_path_parts) :])
        != expected_path_parts
    ):
        raise ContractError("NATIVE_REPORT_ACTUAL_PATH_MISMATCH")
    if (
        actual_native_report.name != "native.json"
        or not actual_native_report.is_file()
        or actual_native_report.is_symlink()
    ):
        raise ContractError(f"NATIVE_REPORT_MISSING_OR_UNSAFE:{actual_native_report}")
    if report["block"]["nativeReportSha256"] != sha256_file(actual_native_report):
        raise ContractError("NATIVE_REPORT_DIGEST_MISMATCH")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan", help="frozen benchmark plan 검증")
    plan_parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    plan_parser.add_argument("--skip-file-digests", action="store_true")
    block_parser = subparsers.add_parser("block", help="한 native block result 검증")
    block_parser.add_argument("report", type=Path)
    block_parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    block_parser.add_argument("--boundary")
    block_parser.add_argument("--selector")
    block_parser.add_argument("--skip-plan-file-digests", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            plan = validate_plan(args.plan, verify_files=not args.skip_file_digests)
            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "caseCount": len(plan["cases"]),
                        "selectorCount": len(plan["familySelectors"]),
                        "rotationCount": len(plan["execution"]["candidateOrderBlocks"]),
                    },
                    sort_keys=True,
                )
            )
        else:
            report = validate_block_result(
                args.report,
                plan_path=args.plan,
                native_report_path=args.report.with_name("native.json"),
                expected_boundary_id=args.boundary,
                expected_selector_id=args.selector,
                verify_plan_files=not args.skip_plan_file_digests,
            )
            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "runId": report["runId"],
                        "selectorId": report["block"]["selectorId"],
                    },
                    sort_keys=True,
                )
            )
    except ContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
