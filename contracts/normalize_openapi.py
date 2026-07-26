from __future__ import annotations

import argparse
import copy
import hashlib
import sys
from pathlib import Path
from typing import Any

from openapi_spec_validator import validate
from openapi_spec_validator.exceptions import OpenAPISpecValidatorError
from openapi_spec_validator.validation.exceptions import OpenAPIValidationError

_SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_REPO_ROOT))

from contracts.generate_principle_contracts import (
    CATALOG_PATH,
    ContractValidationError,
    canonical_json_bytes,
    load_json_bytes_strict,
    validate_catalog_semantics,
)


REPO_ROOT = _SCRIPT_REPO_ROOT
DEFAULT_INPUT = REPO_ROOT / "workspaces" / "decision-platform" / "spring-api" / "build" / "openapi.json"
DEFAULT_EXPECTED = REPO_ROOT / "contracts" / "openapi" / "openapi.json"
OAS_BASE_DIALECT = "https://spec.openapis.org/oas/3.1/dialect/base"
CONTRACT_ID = "s2-1-principle-contract/v1"
S23_CATALOG_PATH = (
    REPO_ROOT / "contracts/catalogs/s2-3-decision-contract.v1.json"
)
S23_CONTRACT_ID = "s2-3-decision-contract/v1"
S32_CATALOG_PATH = (
    REPO_ROOT / "contracts/catalogs/s3-2-internal-paper-contract.v1.json"
)
S32_CONTRACT_ID = "s3-2-internal-paper-contract/v1"
DECISION_PATH_METHODS = {
    "/api/v1/decisions/evaluate-order": {"post"},
    "/api/v1/decisions/{decisionId}": {"get"},
    "/api/v1/decisions/{decisionId}/audit": {"get"},
}
DECISION_COMPONENTS = {
    "S23EvaluateOrderRequest",
    "S23Decision",
    "S23DecisionSuccessResponse",
    "S23DecisionAudit",
    "S23DecisionAuditSuccessResponse",
}
S32_PATH_METHODS = {
    "/api/v1/brokerage/paper/orders": {"post"},
    "/api/v1/brokerage/paper/accounts/{accountId}/balances": {"get"},
    "/api/v1/brokerage/paper/accounts/{accountId}/buyable": {"get"},
    "/api/v1/brokerage/orders/{orderId}": {"get"},
    "/api/v1/brokerage/orders/{orderId}/cancel": {"post"},
}
S32_COMPONENTS = {
    "S32PaperOrderRequest",
    "S32PaperOrder",
    "S32OrderDetail",
    "S32PaperBalance",
    "S32PaperBuyable",
    "S32PaperOrderSuccessResponse",
    "S32OrderDetailSuccessResponse",
    "S32PaperBalanceSuccessResponse",
    "S32PaperBuyableSuccessResponse",
}
HTTP_METHODS = {
    "delete",
    "get",
    "head",
    "options",
    "patch",
    "post",
    "put",
    "trace",
}


class OpenApiNormalizationError(ValueError):
    """Generated OpenAPI가 허용된 root patch 이외의 의미 차이를 가질 때 발생한다."""


def _load_document(raw: bytes, *, source: str) -> dict[str, Any]:
    try:
        value = load_json_bytes_strict(raw, source=source)
    except ContractValidationError as error:
        raise OpenApiNormalizationError(str(error)) from error
    if not isinstance(value, dict):
        raise OpenApiNormalizationError(f"{source}: OpenAPI root must be an object.")
    for key in ("openapi", "jsonSchemaDialect", "info", "paths", "components"):
        if key not in value:
            raise OpenApiNormalizationError(f"{source}: required root field {key} is missing.")
    if not isinstance(value["paths"], dict) or not isinstance(value["components"], dict):
        raise OpenApiNormalizationError(f"{source}: paths and components must be objects.")
    return value


def _catalog_digest(catalog_bytes: bytes) -> str:
    try:
        catalog = load_json_bytes_strict(catalog_bytes, source="catalog")
        validate_catalog_semantics(catalog)
    except ContractValidationError as error:
        raise OpenApiNormalizationError(str(error)) from error
    canonical = canonical_json_bytes(catalog)
    if catalog_bytes != canonical:
        raise OpenApiNormalizationError("Catalog bytes must be canonical before OpenAPI generation.")
    return hashlib.sha256(catalog_bytes).hexdigest()


def _s23_catalog_digest() -> str:
    raw = S23_CATALOG_PATH.read_bytes()
    catalog = load_json_bytes_strict(raw, source="S2.3 Decision catalog")
    if raw != canonical_json_bytes(catalog):
        raise OpenApiNormalizationError(
            "S2.3 Decision catalog bytes must be canonical."
        )
    return hashlib.sha256(raw).hexdigest()


def _s32_catalog_digest() -> str:
    raw = S32_CATALOG_PATH.read_bytes()
    catalog = load_json_bytes_strict(raw, source="S3.2 INTERNAL_PAPER catalog")
    if raw != canonical_json_bytes(catalog):
        raise OpenApiNormalizationError(
            "S3.2 INTERNAL_PAPER catalog bytes must be canonical."
        )
    return hashlib.sha256(raw).hexdigest()


def _assert_contract_roots(
    document: dict[str, Any],
    digest: str,
    *,
    source: str,
    amendment: bool,
) -> None:
    if document.get("jsonSchemaDialect") != OAS_BASE_DIALECT:
        raise OpenApiNormalizationError(f"{source}: OAS 3.1 base dialect is missing or different.")
    if document.get("x-s2-1-contract-id") != CONTRACT_ID:
        raise OpenApiNormalizationError(f"{source}: S2.1 contract ID extension is invalid.")
    if document.get("x-s2-1-contract-sha256") != digest:
        raise OpenApiNormalizationError(f"{source}: S2.1 catalog digest extension is invalid.")
    if document.get("x-s2-3-contract-id") != S23_CONTRACT_ID:
        raise OpenApiNormalizationError(f"{source}: S2.3 contract ID extension is invalid.")
    if document.get("x-s2-3-contract-sha256") != _s23_catalog_digest():
        raise OpenApiNormalizationError(f"{source}: S2.3 catalog digest extension is invalid.")
    if not amendment:
        if document.get("x-s3-2-contract-id") != S32_CONTRACT_ID:
            raise OpenApiNormalizationError(
                f"{source}: S3.2 contract ID extension is invalid."
            )
        if document.get("x-s3-2-contract-sha256") != _s32_catalog_digest():
            raise OpenApiNormalizationError(
                f"{source}: S3.2 catalog digest extension is invalid."
            )


def _assert_no_premature_principle_paths(document: dict[str, Any], *, source: str) -> None:
    paths = document["paths"]
    premature = [
        path
        for path in paths
        if path == "/api/v1/principle-presets" or path.startswith("/api/v1/principles")
    ]
    if premature:
        raise OpenApiNormalizationError(
            f"{source}: amendment must not advertise S2.1 runtime paths."
        )


def _assert_decision_paths(
    document: dict[str, Any], *, source: str, amendment: bool
) -> None:
    paths = document["paths"]
    actual = {
        path: {
            key.lower()
            for key in item
            if isinstance(key, str) and key.lower() in HTTP_METHODS
        }
        for path, item in paths.items()
        if path == "/api/v1/decisions" or path.startswith("/api/v1/decisions/")
    }
    expected = {} if amendment else DECISION_PATH_METHODS
    if actual != expected:
        raise OpenApiNormalizationError(
            f"{source}: Decision paths or methods differ from the approved S2.3 allowlist."
        )


def _assert_decision_components(
    document: dict[str, Any], *, source: str, amendment: bool
) -> None:
    schemas = document["components"].get("schemas", {})
    if not isinstance(schemas, dict):
        raise OpenApiNormalizationError(f"{source}: component schemas must be an object.")
    actual = {name for name in schemas if name.startswith("S23")}
    expected = set() if amendment else DECISION_COMPONENTS
    if actual != expected:
        raise OpenApiNormalizationError(
            f"{source}: S2.3 component names differ from the approved allowlist."
        )


def _assert_s32_paths(
    document: dict[str, Any], *, source: str, amendment: bool
) -> None:
    paths = document["paths"]
    actual = {
        path: {
            key.lower()
            for key in item
            if isinstance(key, str) and key.lower() in HTTP_METHODS
        }
        for path, item in paths.items()
        if path.startswith("/api/v1/brokerage/paper/")
        or path in {
            "/api/v1/brokerage/orders/{orderId}",
            "/api/v1/brokerage/orders/{orderId}/cancel",
        }
    }
    expected = {} if amendment else S32_PATH_METHODS
    if actual != expected:
        raise OpenApiNormalizationError(
            f"{source}: INTERNAL_PAPER paths or methods differ from the approved S3.2 allowlist."
        )


def _assert_s32_components(
    document: dict[str, Any], *, source: str, amendment: bool
) -> None:
    schemas = document["components"].get("schemas", {})
    if not isinstance(schemas, dict):
        raise OpenApiNormalizationError(f"{source}: component schemas must be an object.")
    actual = {name for name in schemas if name.startswith("S32")}
    expected = set() if amendment else S32_COMPONENTS
    if actual != expected:
        raise OpenApiNormalizationError(
            f"{source}: S3.2 component names differ from the approved allowlist."
        )


def _validate_openapi_schema(document: dict[str, Any], *, source: str) -> None:
    try:
        validate(document)
    except (OpenAPISpecValidatorError, OpenAPIValidationError) as error:
        # validator 오류에는 schema instance가 포함될 수 있어 추적 로그에는 stable 분류만 남긴다.
        raise OpenApiNormalizationError(
            f"{source}: OAS 3.1 schema validation failed."
        ) from error


def normalize_generated_openapi(
    generated_bytes: bytes, catalog_bytes: bytes, *, amendment: bool
) -> bytes:
    generated = _load_document(generated_bytes, source="generated OpenAPI")
    digest = _catalog_digest(catalog_bytes)
    if generated.get("openapi") != "3.1.0":
        raise OpenApiNormalizationError(
            "Generated OpenAPI root must be exactly 3.1.0 before the approved patch."
        )
    _assert_contract_roots(
        generated,
        digest,
        source="generated OpenAPI",
        amendment=amendment,
    )
    if amendment:
        _assert_no_premature_principle_paths(generated, source="generated OpenAPI")
    _assert_decision_paths(
        generated,
        source="generated OpenAPI",
        amendment=amendment,
    )
    _assert_decision_components(
        generated,
        source="generated OpenAPI",
        amendment=amendment,
    )
    _assert_s32_paths(
        generated,
        source="generated OpenAPI",
        amendment=amendment,
    )
    _assert_s32_components(
        generated,
        source="generated OpenAPI",
        amendment=amendment,
    )
    _validate_openapi_schema(generated, source="generated OpenAPI")

    normalized = copy.deepcopy(generated)
    normalized["openapi"] = "3.1.1"
    _validate_openapi_schema(normalized, source="normalized OpenAPI")
    return canonical_json_bytes(normalized)


def check_normalized_openapi(
    generated_bytes: bytes,
    expected_bytes: bytes,
    catalog_bytes: bytes,
    *,
    amendment: bool,
) -> bytes:
    normalized = normalize_generated_openapi(
        generated_bytes, catalog_bytes, amendment=amendment
    )
    expected = _load_document(expected_bytes, source="tracked OpenAPI")
    digest = _catalog_digest(catalog_bytes)
    if expected.get("openapi") != "3.1.1":
        raise OpenApiNormalizationError("Tracked OpenAPI root must be exactly 3.1.1.")
    _assert_contract_roots(
        expected,
        digest,
        source="tracked OpenAPI",
        amendment=amendment,
    )
    if amendment:
        _assert_no_premature_principle_paths(expected, source="tracked OpenAPI")
    _assert_decision_paths(
        expected,
        source="tracked OpenAPI",
        amendment=amendment,
    )
    _assert_decision_components(
        expected,
        source="tracked OpenAPI",
        amendment=amendment,
    )
    _assert_s32_paths(
        expected,
        source="tracked OpenAPI",
        amendment=amendment,
    )
    _assert_s32_components(
        expected,
        source="tracked OpenAPI",
        amendment=amendment,
    )
    _validate_openapi_schema(expected, source="tracked OpenAPI")
    if expected_bytes != canonical_json_bytes(expected):
        raise OpenApiNormalizationError("Tracked OpenAPI bytes are not canonical JSON.")
    if normalized != expected_bytes:
        raise OpenApiNormalizationError(
            "Generated OpenAPI differs from tracked canonical beyond the root patch."
        )
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalize only the approved OpenAPI 3.1.0 to 3.1.1 root patch."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--expected", type=Path, default=DEFAULT_EXPECTED)
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    parser.add_argument(
        "--implementation",
        action="store_true",
        help="Require the exact implemented S2.1, S2.3, and S3.2 runtime paths.",
    )
    arguments = parser.parse_args()

    try:
        generated_bytes = arguments.input.read_bytes()
        catalog_bytes = arguments.catalog.read_bytes()
        if arguments.write:
            normalized = normalize_generated_openapi(
                generated_bytes,
                catalog_bytes,
                amendment=not arguments.implementation,
            )
            arguments.expected.parent.mkdir(parents=True, exist_ok=True)
            arguments.expected.write_bytes(normalized)
            print(
                "WROTE "
                + arguments.expected.resolve().relative_to(REPO_ROOT).as_posix()
            )
            return 0

        expected_bytes = arguments.expected.read_bytes()
        check_normalized_openapi(
            generated_bytes,
            expected_bytes,
            catalog_bytes,
            amendment=not arguments.implementation,
        )
    except (OSError, OpenApiNormalizationError) as error:
        print(f"OpenAPI normalization failed: {error}", file=sys.stderr)
        return 1

    print("OpenAPI normalization check succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
