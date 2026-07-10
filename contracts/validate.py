from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "contracts" / "schemas"
EXAMPLES_DIR = REPO_ROOT / "contracts" / "examples"
INVALID_EXAMPLES_DIR = EXAMPLES_DIR / "invalid"


def relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def load_json(path: Path) -> object:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def schema_name_from_example(path: Path, suffix: str) -> str:
    if not path.name.endswith(suffix):
        raise ValueError(f"Unexpected example file name: {relative(path)}")
    # 같은 schema에 여러 negative case를 둘 수 있게 `schema.case.invalid.json`을 지원한다.
    return path.name[: -len(suffix)].split(".", maxsplit=1)[0]


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


def validate_valid_examples(validators: dict[str, tuple[Path, Draft202012Validator]]) -> int:
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
        if error is None:
            print(f"PASS {relative(example_path)} against {relative(schema_path)}")
            continue

        failures += 1
        print(
            f"FAIL {relative(example_path)} against {relative(schema_path)}: "
            f"{error_location(error)} {error.message}",
            file=sys.stderr,
        )

    return failures


def validate_invalid_examples(validators: dict[str, tuple[Path, Draft202012Validator]]) -> int:
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
        if error is not None:
            print(
                f"PASS expected invalid {relative(example_path)} against {relative(schema_path)}: "
                f"{error_location(error)} {error.message}"
            )
            continue

        failures += 1
        print(
            f"FAIL expected invalid {relative(example_path)} but it passed "
            f"{relative(schema_path)}",
            file=sys.stderr,
        )

    return failures


def main() -> int:
    validators = build_validators()
    failures = validate_valid_examples(validators)
    failures += validate_invalid_examples(validators)
    if failures:
        print(f"contracts validation failed: {failures} failure(s)", file=sys.stderr)
        return 1

    print("contracts validation succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
