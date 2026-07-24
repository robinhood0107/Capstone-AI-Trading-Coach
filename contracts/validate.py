from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from contracts.generate_principle_contracts import (  # noqa: E402
    CATALOG_PATH,
    ContractValidationError,
    load_catalog,
    load_json_bytes_strict,
    validate_principle_payload_semantics,
)
from contracts.generate_s2_2_contracts import (  # noqa: E402
    CATALOG_PATH as S2_2_CATALOG_PATH,
    load_catalog as load_s2_2_catalog,
    validate_risk_decision_semantics,
)
from contracts.generate_s2_3_contracts import (  # noqa: E402
    CATALOG_PATH as S2_3_CATALOG_PATH,
    load_catalog as load_s2_3_catalog,
    validate_decision_response_semantics,
    validate_request_semantics,
)

SCHEMA_DIR = REPO_ROOT / "contracts" / "schemas"
EXAMPLES_DIR = REPO_ROOT / "contracts" / "examples"
INVALID_EXAMPLES_DIR = EXAMPLES_DIR / "invalid"
PAIR_EXAMPLES_DIR = EXAMPLES_DIR / "pairs"

S2_EXAMPLE_SCHEMA_PREFIXES = {
    "principle-create-custom-rules": "principle-create-request",
    "principle-create": "principle-create-request",
    "principle-update-no-op": "principle-update-request",
    "principle-update": "principle-update-request",
    "principle-presets": "principle-preset-list",
    "principle-list-next-page": "principle-list-response",
    "principle-list-empty": "principle-list-response",
    "principle-list": "principle-list-response",
    "principle-history-next-page": "principle-history-response",
    "principle-history-empty": "principle-history-response",
    "principle-history": "principle-history-response",
    "principle-error-version-exhausted": "principle-error",
    "principle-error-payload-too-large": "principle-error",
    "principle-error-unauthorized": "principle-error",
    "principle-error-validation": "principle-error",
    "principle-error-not-found": "principle-error",
    "principle-error-forbidden": "principle-error",
    "principle-error-conflict": "principle-error",
    "principle-error-cursor": "principle-error",
}


def relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def load_json(path: Path) -> object:
    return load_json_bytes_strict(path.read_bytes(), source=relative(path))


def schema_name_from_example(path: Path, suffix: str) -> str:
    if not path.name.endswith(suffix):
        raise ValueError(f"Unexpected example file name: {relative(path)}")
    base_name = path.name[: -len(suffix)].split(".", maxsplit=1)[0]
    return S2_EXAMPLE_SCHEMA_PREFIXES.get(base_name, base_name)


def first_error(errors: Iterable[ValidationError]) -> ValidationError | None:
    ordered = sorted(errors, key=lambda error: list(error.path))
    return ordered[0] if ordered else None


def error_location(error: ValidationError) -> str:
    if not error.path:
        return "$"
    return "$." + ".".join(str(part) for part in error.path)


def build_validators() -> dict[str, tuple[Path, Draft202012Validator]]:
    validators: dict[str, tuple[Path, Draft202012Validator]] = {}
    for schema_path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        schema = load_json(schema_path)
        Draft202012Validator.check_schema(schema)
        schema_name = schema_path.name.removesuffix(".schema.json")
        validators[schema_name] = (
            schema_path,
            Draft202012Validator(schema, format_checker=FormatChecker()),
        )
    return validators


def validate_example_semantics(
    schema_name: str,
    example: object,
    principle_catalog: object,
    s2_2_catalog: object,
    s2_3_catalog: object,
) -> None:
    if schema_name == "risk_decision":
        validate_risk_decision_semantics(example, s2_2_catalog)
        return
    if schema_name == "s2-3-evaluate-order-request":
        validate_request_semantics(example)
        return
    if schema_name == "s2-3-decision-response":
        validate_decision_response_semantics(example, s2_2_catalog)
        return
    validate_principle_payload_semantics(
        schema_name,
        example,
        principle_catalog,
    )


def validate_valid_examples(
    validators: dict[str, tuple[Path, Draft202012Validator]],
    principle_catalog: object,
    s2_2_catalog: object,
    s2_3_catalog: object,
) -> int:
    failures = 0
    valid_examples = sorted(EXAMPLES_DIR.glob("*.valid.json"))
    if not valid_examples:
        print("FAIL no valid examples found in contracts/examples", file=sys.stderr)
        return 1

    for example_path in valid_examples:
        schema_name = schema_name_from_example(example_path, ".valid.json")
        schema_path, validator = validators[schema_name]
        example = load_json(example_path)
        error = first_error(validator.iter_errors(example))
        semantic_error: ContractValidationError | None = None
        if error is None:
            try:
                validate_example_semantics(
                    schema_name,
                    example,
                    principle_catalog,
                    s2_2_catalog,
                    s2_3_catalog,
                )
            except ContractValidationError as caught:
                semantic_error = caught
        if error is None and semantic_error is None:
            print(f"PASS {relative(example_path)} against {relative(schema_path)}")
            continue

        failures += 1
        message = (
            semantic_error
            if semantic_error is not None
            else f"{error_location(error)} {error.message}"
        )
        print(
            f"FAIL {relative(example_path)} against {relative(schema_path)}: "
            f"{message}",
            file=sys.stderr,
        )

    return failures


def validate_invalid_examples(
    validators: dict[str, tuple[Path, Draft202012Validator]],
    principle_catalog: object,
    s2_2_catalog: object,
    s2_3_catalog: object,
) -> int:
    failures = 0
    invalid_examples = sorted(INVALID_EXAMPLES_DIR.glob("*.invalid.json"))
    if not invalid_examples:
        print("FAIL no invalid examples found in contracts/examples/invalid", file=sys.stderr)
        return 1

    for example_path in invalid_examples:
        schema_name = schema_name_from_example(example_path, ".invalid.json")
        schema_path, validator = validators[schema_name]
        example = load_json(example_path)
        error = first_error(validator.iter_errors(example))
        semantic_error: ContractValidationError | None = None
        if error is None:
            try:
                validate_example_semantics(
                    schema_name,
                    example,
                    principle_catalog,
                    s2_2_catalog,
                    s2_3_catalog,
                )
            except ContractValidationError as caught:
                semantic_error = caught
        if error is not None or semantic_error is not None:
            message = (
                semantic_error
                if semantic_error is not None
                else f"{error_location(error)} {error.message}"
            )
            print(
                f"PASS expected invalid {relative(example_path)} against {relative(schema_path)}: "
                f"{message}"
            )
            continue

        failures += 1
        print(
            f"FAIL expected invalid {relative(example_path)} but it passed "
            f"{relative(schema_path)}",
            file=sys.stderr,
        )

    return failures


def _load_naver_pair(path: Path) -> tuple[object, object]:
    pair = load_json(path)
    if not isinstance(pair, dict) or set(pair) != {"snapshotExample", "manifestExample"}:
        raise ValueError("Naver pair fixture must contain two example references")
    snapshot_name = pair["snapshotExample"]
    manifest_name = pair["manifestExample"]
    if any(
        not isinstance(name, str)
        or not name.endswith(".valid.json")
        or "/" in name
        or "\\" in name
        for name in (snapshot_name, manifest_name)
    ):
        raise ValueError("Naver pair fixture reference is invalid")
    snapshot_path = EXAMPLES_DIR / snapshot_name
    manifest_path = EXAMPLES_DIR / manifest_name
    if not snapshot_path.is_file() or not manifest_path.is_file():
        raise ValueError("Naver pair fixture reference is unavailable")
    return load_json(snapshot_path), load_json(manifest_path)


def _naver_pair_query_counts_match(snapshot: object, manifest: object) -> bool:
    if not isinstance(snapshot, dict) or not isinstance(manifest, dict):
        return False
    queries = snapshot.get("queries")
    count_breakdown = manifest.get("countBreakdown")
    if not isinstance(queries, list) or not isinstance(count_breakdown, dict):
        return False
    query_count = count_breakdown.get("queryCount")
    return (
        isinstance(query_count, int)
        and not isinstance(query_count, bool)
        and query_count == len(queries)
    )


def validate_naver_pair_examples(
    validators: dict[str, tuple[Path, Draft202012Validator]],
) -> int:
    """JSON Schema 밖의 snapshot/manifest query-count 교차 계약을 fixture pair로 검증한다."""
    failures = 0
    pair_examples = [(path, True) for path in sorted(PAIR_EXAMPLES_DIR.glob("*.valid.json"))]
    pair_examples.extend(
        (path, False) for path in sorted(PAIR_EXAMPLES_DIR.glob("*.invalid.json"))
    )
    if not pair_examples:
        print("FAIL no Naver pair examples found in contracts/examples/pairs", file=sys.stderr)
        return 1

    snapshot_validator = validators["naver_news_metadata_snapshot"][1]
    manifest_validator = validators["source_snapshot_manifest"][1]
    for example_path, expected_valid in pair_examples:
        try:
            snapshot, manifest = _load_naver_pair(example_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            failures += 1
            print(f"FAIL invalid Naver pair fixture {relative(example_path)}", file=sys.stderr)
            continue

        snapshot_error = first_error(snapshot_validator.iter_errors(snapshot))
        manifest_error = first_error(manifest_validator.iter_errors(manifest))
        if snapshot_error is not None or manifest_error is not None:
            failures += 1
            print(
                f"FAIL Naver pair fixture references schema-invalid examples: "
                f"{relative(example_path)}",
                file=sys.stderr,
            )
            continue

        matches = _naver_pair_query_counts_match(snapshot, manifest)
        if matches == expected_valid:
            expectation = "valid" if expected_valid else "invalid"
            print(f"PASS expected {expectation} Naver pair {relative(example_path)}")
            continue

        failures += 1
        expectation = "match" if expected_valid else "mismatch"
        print(
            f"FAIL expected Naver pair query-count {expectation}: {relative(example_path)}",
            file=sys.stderr,
        )

    return failures


def main() -> int:
    principle_catalog = load_catalog(CATALOG_PATH)
    s2_2_catalog = load_s2_2_catalog(S2_2_CATALOG_PATH)
    s2_3_catalog = load_s2_3_catalog(S2_3_CATALOG_PATH)
    validators = build_validators()
    failures = validate_valid_examples(
        validators,
        principle_catalog,
        s2_2_catalog,
        s2_3_catalog,
    )
    failures += validate_invalid_examples(
        validators,
        principle_catalog,
        s2_2_catalog,
        s2_3_catalog,
    )
    failures += validate_naver_pair_examples(validators)
    if failures:
        print(f"contracts validation failed: {failures} failure(s)", file=sys.stderr)
        return 1

    print("contracts validation succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
