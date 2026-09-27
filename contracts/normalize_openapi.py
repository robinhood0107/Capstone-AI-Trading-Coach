from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
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
from contracts.generated_artifact_io import write_generated_path


REPO_ROOT = _SCRIPT_REPO_ROOT
DEFAULT_INPUT = (
    REPO_ROOT
    / "workspaces"
    / "decision-platform"
    / "spring-api"
    / "build"
    / "openapi.json"
)
DEFAULT_EXPECTED = REPO_ROOT / "contracts" / "openapi" / "openapi.json"
FULL_AUTH_OVERLAY = REPO_ROOT / "contracts" / "openapi" / "mars-full-auth.v1.openapi.json"
FULL_ADMIN_OVERLAY = REPO_ROOT / "contracts" / "openapi" / "mars-full-admin.v1.openapi.json"
OAS_BASE_DIALECT = "https://spec.openapis.org/oas/3.1/dialect/base"
CANONICAL_GENERATED_SERVER = {
    "description": "Generated server url",
    "url": "http://127.0.0.1:18080",
}
_LOOPBACK_GENERATED_SERVER_RE = re.compile(r"http://127\.0\.0\.1:([0-9]{4,5})")
CONTRACT_ID = "s2-1-principle-contract/v1"
S23_CATALOG_PATH = REPO_ROOT / "contracts/catalogs/s2-3-decision-contract.v1.json"
S23_CONTRACT_ID = "s2-3-decision-contract/v1"
S32_CATALOG_PATH = REPO_ROOT / "contracts/catalogs/s3-2-internal-paper-contract.v1.json"
S32_CONTRACT_ID = "s3-2-internal-paper-contract/v1"
S33_CATALOG_PATH = REPO_ROOT / "contracts/catalogs/s3-3-fill-contract.v1.json"
S33_CONTRACT_ID = "s3-3-fill-contract/v1"
S31_PATH_METHODS = {
    "/api/v1/brokerage/mock/orders": {"post"},
    "/api/v1/brokerage/mock/accounts/{accountId}/balances": {"get"},
    "/api/v1/brokerage/mock/accounts/{accountId}/buyable": {"get"},
}
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
S33_PATH_METHODS = {
    "/api/v1/brokerage/orders/{orderId}/reconcile": {"post"},
    "/api/v1/brokerage/mock/accounts/{accountId}/fills": {"get"},
    "/api/v1/brokerage/paper/accounts/{accountId}/fills": {"get"},
}
BROKERAGE_PATH_METHODS = {
    **S31_PATH_METHODS,
    **S32_PATH_METHODS,
    **S33_PATH_METHODS,
}
S33_COMPONENTS = {
    "S33FillObservation",
    "S33Reconcile",
    "S33FillPage",
    "S33ReconcileSuccessResponse",
    "S33FillPageSuccessResponse",
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


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OpenApiNormalizationError(f"{label} must be an object.")
    return value


def _load_document(raw: bytes, *, source: str) -> dict[str, Any]:
    try:
        value = load_json_bytes_strict(raw, source=source)
    except ContractValidationError as error:
        raise OpenApiNormalizationError(str(error)) from error
    if not isinstance(value, dict):
        raise OpenApiNormalizationError(f"{source}: OpenAPI root must be an object.")
    for key in ("openapi", "jsonSchemaDialect", "info", "paths", "components"):
        if key not in value:
            raise OpenApiNormalizationError(
                f"{source}: required root field {key} is missing."
            )
    if not isinstance(value["paths"], dict) or not isinstance(
        value["components"], dict
    ):
        raise OpenApiNormalizationError(
            f"{source}: paths and components must be objects."
        )
    return value


def _catalog_digest(catalog_bytes: bytes) -> str:
    try:
        catalog = load_json_bytes_strict(catalog_bytes, source="catalog")
        validate_catalog_semantics(catalog)
    except ContractValidationError as error:
        raise OpenApiNormalizationError(str(error)) from error
    canonical = canonical_json_bytes(catalog)
    if catalog_bytes != canonical:
        raise OpenApiNormalizationError(
            "Catalog bytes must be canonical before OpenAPI generation."
        )
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


def _s33_catalog_digest() -> str:
    raw = S33_CATALOG_PATH.read_bytes()
    catalog = load_json_bytes_strict(raw, source="S3.3 fill contract catalog")
    if raw != canonical_json_bytes(catalog):
        raise OpenApiNormalizationError(
            "S3.3 fill contract catalog bytes must be canonical."
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
        raise OpenApiNormalizationError(
            f"{source}: OAS 3.1 base dialect is missing or different."
        )
    if document.get("x-s2-1-contract-id") != CONTRACT_ID:
        raise OpenApiNormalizationError(
            f"{source}: S2.1 contract ID extension is invalid."
        )
    if document.get("x-s2-1-contract-sha256") != digest:
        raise OpenApiNormalizationError(
            f"{source}: S2.1 catalog digest extension is invalid."
        )
    if document.get("x-s2-3-contract-id") != S23_CONTRACT_ID:
        raise OpenApiNormalizationError(
            f"{source}: S2.3 contract ID extension is invalid."
        )
    if document.get("x-s2-3-contract-sha256") != _s23_catalog_digest():
        raise OpenApiNormalizationError(
            f"{source}: S2.3 catalog digest extension is invalid."
        )
    if not amendment:
        if document.get("x-s3-2-contract-id") != S32_CONTRACT_ID:
            raise OpenApiNormalizationError(
                f"{source}: S3.2 contract ID extension is invalid."
            )
        if document.get("x-s3-2-contract-sha256") != _s32_catalog_digest():
            raise OpenApiNormalizationError(
                f"{source}: S3.2 catalog digest extension is invalid."
            )
        if document.get("x-s3-3-contract-id") != S33_CONTRACT_ID:
            raise OpenApiNormalizationError(
                f"{source}: S3.3 contract ID extension is invalid."
            )
        if document.get("x-s3-3-contract-sha256") != _s33_catalog_digest():
            raise OpenApiNormalizationError(
                f"{source}: S3.3 catalog digest extension is invalid."
            )


def _assert_no_premature_principle_paths(
    document: dict[str, Any], *, source: str
) -> None:
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
        raise OpenApiNormalizationError(
            f"{source}: component schemas must be an object."
        )
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
        if (path.startswith("/api/v1/brokerage/paper/") and not path.endswith("/fills"))
        or path
        in {
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
        raise OpenApiNormalizationError(
            f"{source}: component schemas must be an object."
        )
    actual = {name for name in schemas if name.startswith("S32")}
    expected = set() if amendment else S32_COMPONENTS
    if actual != expected:
        raise OpenApiNormalizationError(
            f"{source}: S3.2 component names differ from the approved allowlist."
        )


def _assert_s33_paths(
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
        if path.startswith("/api/v1/brokerage/")
    }
    expected = {} if amendment else BROKERAGE_PATH_METHODS
    if actual != expected:
        raise OpenApiNormalizationError(
            f"{source}: brokerage paths or methods differ from the approved S3.1-S3.3 allowlist."
        )


def _assert_s33_components(
    document: dict[str, Any], *, source: str, amendment: bool
) -> None:
    schemas = document["components"].get("schemas", {})
    if not isinstance(schemas, dict):
        raise OpenApiNormalizationError(
            f"{source}: component schemas must be an object."
        )
    actual = {name for name in schemas if name.startswith("S33")}
    expected = set() if amendment else S33_COMPONENTS
    if actual != expected:
        raise OpenApiNormalizationError(
            f"{source}: S3.3 component names differ from the approved allowlist."
        )


def _validate_openapi_schema(document: dict[str, Any], *, source: str) -> None:
    try:
        validate(document)
    except (OpenAPISpecValidatorError, OpenAPIValidationError) as error:
        # validator 오류에는 schema instance가 포함될 수 있어 추적 로그에는 stable 분류만 남긴다.
        raise OpenApiNormalizationError(
            f"{source}: OAS 3.1 schema validation failed."
        ) from error


def _schema_refs(value: object) -> set[str]:
    references: set[str] = set()
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
            name = reference.removeprefix("#/components/schemas/")
            if name and "/" not in name:
                references.add(name)
        for child in value.values():
            references.update(_schema_refs(child))
    elif isinstance(value, list):
        for child in value:
            references.update(_schema_refs(child))
    return references


def _project_full_only_operations(
    generated: dict[str, Any], expected: dict[str, Any]
) -> None:
    """Keep FULL-specific auth/admin routes in their dedicated contracts.

    The root OpenAPI is the private execution contract. Spring's documentation profile
    sees some FULL controllers as well, so project those paths out before comparing the
    root. Any overlapping operation stays only when its implementation operationId still
    matches the root contract (for example, LOCAL's /auth/login).
    """
    root_paths = _object(generated.get("paths"), "generated paths")
    expected_paths = _object(expected.get("paths"), "expected paths")
    full_operations: set[tuple[str, str]] = set()
    full_only_schema_roots: set[str] = set()
    for path in (FULL_AUTH_OVERLAY, FULL_ADMIN_OVERLAY):
        overlay = json.loads(path.read_text(encoding="utf-8"))
        for endpoint, item in _object(overlay.get("paths"), f"{path.name} paths").items():
            for method in item:
                if method in HTTP_METHODS:
                    full_operations.add((endpoint, method))
        overlay_components = _object(
            _object(overlay.get("components", {}), f"{path.name} components").get(
                "schemas", {}
            ),
            f"{path.name} schemas",
        )
        full_only_schema_roots.update(overlay_components)

    for path, method in full_operations:
        item = root_paths.get(path)
        if not isinstance(item, dict) or method not in item:
            continue
        root_operation = _object(item[method], f"generated {method} {path}")
        expected_item = expected_paths.get(path)
        expected_operation = (
            expected_item.get(method)
            if isinstance(expected_item, dict)
            else None
        )
        if (
            isinstance(expected_operation, dict)
            and expected_operation.get("operationId") == root_operation.get("operationId")
        ):
            continue
        full_only_schema_roots.update(_schema_refs(root_operation))
        item.pop(method)
        if not any(key in HTTP_METHODS for key in item):
            full_only_schema_roots.update(_schema_refs(item))
            root_paths.pop(path)

    schemas = _object(
        _object(generated.get("components"), "generated components").get("schemas"),
        "generated schemas",
    )
    expected_schemas = _object(
        _object(expected.get("components"), "expected components").get("schemas"),
        "expected schemas",
    )

    # Drop only schema components reachable from the approved FULL-only operations removed
    # above. An unrelated or newly injected generated component must fail closed below.
    full_only_schemas: set[str] = set()
    pending_full_only = list(full_only_schema_roots)
    while pending_full_only:
        name = pending_full_only.pop()
        if name in expected_schemas or name in full_only_schemas:
            continue
        schema = schemas.get(name)
        if schema is None:
            continue
        full_only_schemas.add(name)
        pending_full_only.extend(_schema_refs(schema))

    reachable = _schema_refs(generated["paths"])
    visited: set[str] = set()
    while reachable - visited:
        name = (reachable - visited).pop()
        visited.add(name)
        schema = schemas.get(name)
        if schema is not None:
            reachable.update(_schema_refs(schema))
    unexpected = reachable - set(expected_schemas)
    if unexpected:
        raise OpenApiNormalizationError(
            "FULL route projection left uncontracted private-root schemas: "
            + ", ".join(sorted(unexpected))
        )
    uncontracted = set(schemas) - set(expected_schemas) - full_only_schemas
    if uncontracted:
        raise OpenApiNormalizationError(
            "Generated OpenAPI contains unapproved component schemas: "
            + ", ".join(sorted(uncontracted))
        )
    for name in full_only_schemas:
        schemas.pop(name)


def normalize_generated_openapi(
    generated_bytes: bytes,
    catalog_bytes: bytes,
    *,
    amendment: bool,
    expected_bytes: bytes | None = None,
) -> bytes:
    generated = _load_document(generated_bytes, source="generated OpenAPI")
    if expected_bytes is not None:
        expected = _load_document(expected_bytes, source="tracked OpenAPI")
        _project_full_only_operations(generated, expected)
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
    _assert_s33_paths(
        generated,
        source="generated OpenAPI",
        amendment=amendment,
    )
    _assert_s33_components(
        generated,
        source="generated OpenAPI",
        amendment=amendment,
    )
    _validate_openapi_schema(generated, source="generated OpenAPI")

    normalized = copy.deepcopy(generated)
    normalized["openapi"] = "3.1.1"
    servers = normalized.get("servers")
    if servers is not None:
        if (
            not isinstance(servers, list)
            or len(servers) != 1
            or not isinstance(servers[0], dict)
        ):
            raise OpenApiNormalizationError(
                "generated OpenAPI: servers must be the single generated loopback server."
            )
        generated_server = servers[0]
        match = _LOOPBACK_GENERATED_SERVER_RE.fullmatch(
            str(generated_server.get("url", ""))
        )
        if (
            set(generated_server) != {"description", "url"}
            or generated_server.get("description") != "Generated server url"
            or match is None
            or int(match.group(1)) not in range(1024, 65536)
        ):
            raise OpenApiNormalizationError(
                "generated OpenAPI: server is not the approved unprivileged loopback endpoint."
            )
        normalized["servers"] = [copy.deepcopy(CANONICAL_GENERATED_SERVER)]
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
        generated_bytes,
        catalog_bytes,
        amendment=amendment,
        expected_bytes=expected_bytes,
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
    _assert_s33_paths(
        expected,
        source="tracked OpenAPI",
        amendment=amendment,
    )
    _assert_s33_components(
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
        description="Normalize the approved OpenAPI version and generated loopback server roots."
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
        help="Require the exact implemented S2.1, S2.3, S3.2, and S3.3 runtime paths.",
    )
    arguments = parser.parse_args()

    try:
        generated_bytes = arguments.input.read_bytes()
        catalog_bytes = arguments.catalog.read_bytes()
        if arguments.write:
            expected_bytes = arguments.expected.read_bytes()
            normalized = normalize_generated_openapi(
                generated_bytes,
                catalog_bytes,
                amendment=not arguments.implementation,
                expected_bytes=expected_bytes,
            )
            write_generated_path(REPO_ROOT, arguments.expected, normalized)
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
