from __future__ import annotations

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
from contracts.generate_s4_rag_contracts import (  # noqa: E402
    CATALOG_PATH as S4_RAG_CATALOG_PATH,
    load_catalog as load_s4_rag_catalog,
    validate_admin_policy_selection_semantics,
    validate_catalog_semantics as validate_s4_rag_catalog_semantics,
    validate_rag_ask_request_semantics,
    validate_rag_source_card_semantics,
)
from contracts.generate_rag_source_card_v2_contracts import (  # noqa: E402
    validate_rag_source_card_v2_semantics,
)
from contracts.generate_s1_3g_news_contracts import (  # noqa: E402
    validate_gdelt_observation_semantics,
    validate_news_summary_semantics,
)
from contracts.generate_s4_8a_cross_market_contracts import (  # noqa: E402
    SCHEMA_IDS as S4_8A_SCHEMA_IDS,
    validate_semantics as validate_s4_8a_semantics,
)
from contracts.generate_s5_0_signal_v2_contracts import (  # noqa: E402
    validate_signal_v2_semantics,
)

SCHEMA_DIR = REPO_ROOT / "contracts" / "schemas"
EXAMPLES_DIR = REPO_ROOT / "contracts" / "examples"
INVALID_EXAMPLES_DIR = EXAMPLES_DIR / "invalid"

S2_EXAMPLE_SCHEMA_PREFIXES = {
    "gdelt_news_tone_observation": "gdelt_news_tone_observation.v1",
    "news_sentiment_summary": "news_sentiment_summary.v2",
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

VERSIONED_EXAMPLE_SCHEMAS = {
    **{schema_id: schema_id for schema_id in S4_8A_SCHEMA_IDS},
    "s2-2-hash-vector.v3": "s2-2-hash-vector.v3",
}

S4_5_PROVIDER_PACKET_SCHEMAS = {
    "s4-2c-voyage-approval",
    "s4-4g-gemini-approval",
}


def relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def load_json(path: Path) -> object:
    return load_json_bytes_strict(path.read_bytes(), source=relative(path))


def schema_name_from_example(path: Path, suffix: str) -> str:
    if not path.name.endswith(suffix):
        raise ValueError(f"Unexpected example file name: {relative(path)}")
    example_name = path.name[: -len(suffix)]
    for versioned_prefix, schema_name in VERSIONED_EXAMPLE_SCHEMAS.items():
        if example_name == versioned_prefix or example_name.startswith(
            f"{versioned_prefix}."
        ):
            return schema_name
    base_name = example_name.split(".", maxsplit=1)[0]
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
    s4_rag_catalog: object,
) -> None:
    if schema_name in S4_5_PROVIDER_PACKET_SCHEMAS:
        # Packet의 zero-paid/store=false/purpose 불변식은 closed JSON Schema가 직접 고정한다.
        return
    if schema_name == "risk_decision":
        validate_risk_decision_semantics(example, s2_2_catalog)
        return
    if schema_name == "s2-3-evaluate-order-request":
        validate_request_semantics(example)
        return
    if schema_name == "s2-3-decision-response":
        validate_decision_response_semantics(example, s2_2_catalog)
        return
    if schema_name == "s3-1-mock-order-request":
        validate_s3_1_mock_order_request_semantics(example)
        return
    if schema_name == "s3-2-paper-order-request":
        validate_s3_2_paper_order_request_semantics(example)
        return
    if schema_name == "s4-rag-contract":
        if not isinstance(example, dict):
            raise ContractValidationError("S4 RAG contract example must be an object.")
        validate_s4_rag_catalog_semantics(example)
        return
    if schema_name == "s4-rag-ask-request":
        if not isinstance(s4_rag_catalog, dict):
            raise ContractValidationError("S4 RAG catalog must be available.")
        validate_rag_ask_request_semantics(example, s4_rag_catalog)
        return
    if schema_name == "s4-rag-admin-policy-selection":
        if not isinstance(s4_rag_catalog, dict):
            raise ContractValidationError("S4 RAG catalog must be available.")
        validate_admin_policy_selection_semantics(example, s4_rag_catalog)
        return
    if schema_name == "rag-source-card-v1":
        validate_rag_source_card_semantics(example)
        return
    if schema_name == "rag-source-card-v2":
        validate_rag_source_card_v2_semantics(example)
        return
    if schema_name == "gdelt_news_tone_observation.v1":
        validate_gdelt_observation_semantics(example)
        return
    if schema_name == "news_sentiment_summary.v2":
        validate_news_summary_semantics(example)
        return
    if schema_name in S4_8A_SCHEMA_IDS:
        if not isinstance(example, dict):
            raise ContractValidationError("S4.8A contract example must be an object.")
        validate_s4_8a_semantics(schema_name, example)
        return
    if schema_name == "signal-v2":
        validate_signal_v2_semantics(example)
        return
    validate_principle_payload_semantics(
        schema_name,
        example,
        principle_catalog,
    )


def validate_s3_1_mock_order_request_semantics(example: object) -> None:
    validate_brokerage_order_request_semantics(example, session="S3.1 mock")


def validate_s3_2_paper_order_request_semantics(example: object) -> None:
    validate_brokerage_order_request_semantics(example, session="S3.2 paper")


def validate_brokerage_order_request_semantics(
    example: object, *, session: str
) -> None:
    if not isinstance(example, dict):
        raise ContractValidationError(f"{session} order request must be an object.")
    order = example.get("orderIntent")
    if not isinstance(order, dict):
        raise ContractValidationError(f"{session} orderIntent must be an object.")
    quantity = order.get("quantity")
    price = order.get("estimatedPrice")
    amount = order.get("estimatedAmount")
    if (
        not isinstance(quantity, int)
        or isinstance(quantity, bool)
        or not isinstance(price, int)
        or isinstance(price, bool)
        or not isinstance(amount, int)
        or isinstance(amount, bool)
    ):
        raise ContractValidationError(
            f"{session} orderIntent numeric fields must be integers."
        )
    if quantity * price != amount:
        raise ContractValidationError(
            f"{session} estimatedAmount must equal quantity * estimatedPrice."
        )


def validate_valid_examples(
    validators: dict[str, tuple[Path, Draft202012Validator]],
    principle_catalog: object,
    s2_2_catalog: object,
    s2_3_catalog: object,
    s4_rag_catalog: object,
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
                    s4_rag_catalog,
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
            f"FAIL {relative(example_path)} against {relative(schema_path)}: {message}",
            file=sys.stderr,
        )

    return failures


def validate_invalid_examples(
    validators: dict[str, tuple[Path, Draft202012Validator]],
    principle_catalog: object,
    s2_2_catalog: object,
    s2_3_catalog: object,
    s4_rag_catalog: object,
) -> int:
    failures = 0
    invalid_examples = sorted(INVALID_EXAMPLES_DIR.glob("*.invalid.json"))
    if not invalid_examples:
        print(
            "FAIL no invalid examples found in contracts/examples/invalid",
            file=sys.stderr,
        )
        return 1

    for example_path in invalid_examples:
        schema_name = schema_name_from_example(example_path, ".invalid.json")
        schema_path, validator = validators[schema_name]
        try:
            example = load_json(example_path)
        except ContractValidationError as error:
            print(
                f"PASS expected invalid {relative(example_path)} against "
                f"{relative(schema_path)}: {error}"
            )
            continue
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
                    s4_rag_catalog,
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


def main() -> int:
    principle_catalog = load_catalog(CATALOG_PATH)
    s2_2_catalog = load_s2_2_catalog(S2_2_CATALOG_PATH)
    s2_3_catalog = load_s2_3_catalog(S2_3_CATALOG_PATH)
    s4_rag_catalog = load_s4_rag_catalog(S4_RAG_CATALOG_PATH)
    validators = build_validators()
    failures = validate_valid_examples(
        validators,
        principle_catalog,
        s2_2_catalog,
        s2_3_catalog,
        s4_rag_catalog,
    )
    failures += validate_invalid_examples(
        validators,
        principle_catalog,
        s2_2_catalog,
        s2_3_catalog,
        s4_rag_catalog,
    )
    if failures:
        print(f"contracts validation failed: {failures} failure(s)", file=sys.stderr)
        return 1

    print("contracts validation succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
