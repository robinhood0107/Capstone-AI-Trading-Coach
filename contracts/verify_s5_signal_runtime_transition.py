"""S5 Signal runtime이 Pre-S5 OpenAPI에 허용된 추가만 했는지 검증한다."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, Final, Mapping

from contracts.generate_principle_contracts import (
    ContractValidationError,
    canonical_json_bytes,
    load_json_bytes_strict,
)


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH: Final = ROOT / "contracts/catalogs/s5-signal-runtime-transition.v1.json"
S7_S8_CATALOG_PATH: Final = ROOT / "contracts/catalogs/s7-s8-openapi-transition.v1.json"
OPENAPI_PATH: Final = ROOT / "contracts/openapi/openapi.json"


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractValidationError(f"{label} must be an object.")
    return value


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) for item in value)
    ):
        raise ContractValidationError(f"{label} must be a non-empty string list.")
    if len(value) != len(set(value)):
        raise ContractValidationError(f"{label} must not contain duplicates.")
    return tuple(value)


def load_transition_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    """canonical transition catalog을 strict JSON으로 읽는다."""

    if not path.is_file() or path.is_symlink():
        raise ContractValidationError(
            "S5 Signal runtime transition catalog is unavailable."
        )
    raw = path.read_bytes()
    catalog = _object(
        load_json_bytes_strict(raw, source=str(path)), "transition catalog"
    )
    if raw != canonical_json_bytes(catalog):
        raise ContractValidationError(
            "S5 Signal runtime transition catalog is not canonical."
        )
    required = {
        "contractId",
        "historicalOpenApiRawSha256",
        "historicalPreservedProjectionSha256",
        "allowedPath",
        "allowedMethods",
        "allowedSchemaNames",
        "allowedRootTags",
    }
    if set(catalog) != required:
        raise ContractValidationError(
            "S5 Signal runtime transition catalog fields drifted."
        )
    if catalog["contractId"] != "s5-signal-runtime-transition.v1":
        raise ContractValidationError(
            "S5 Signal runtime transition contract ID drifted."
        )
    if catalog["allowedPath"] != "/api/v2/signals/{symbol}":
        raise ContractValidationError("S5 Signal runtime allowed path drifted.")
    if _string_list(catalog["allowedMethods"], "allowedMethods") != ("get",):
        raise ContractValidationError("S5 Signal runtime allowed method drifted.")
    _string_list(catalog["allowedSchemaNames"], "allowedSchemaNames")
    _string_list(catalog["allowedRootTags"], "allowedRootTags")
    for field in ("historicalOpenApiRawSha256", "historicalPreservedProjectionSha256"):
        value = catalog[field]
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise ContractValidationError(
                f"{field} must be a lowercase SHA-256 digest."
            )
    return catalog


def load_s7_s8_transition_catalog(path: Path = S7_S8_CATALOG_PATH) -> dict[str, Any]:
    """승인된 S7/S8 additive OpenAPI fragment의 exact identity를 읽는다."""

    if not path.is_file() or path.is_symlink():
        raise ContractValidationError("S7/S8 OpenAPI transition catalog is unavailable.")
    raw = path.read_bytes()
    catalog = _object(load_json_bytes_strict(raw, source=str(path)), "S7/S8 transition catalog")
    if raw != canonical_json_bytes(catalog):
        raise ContractValidationError("S7/S8 OpenAPI transition catalog is not canonical.")
    if set(catalog) != {"contractId", "allowedPaths", "allowedSchemaNames", "additiveProjectionSha256"}:
        raise ContractValidationError("S7/S8 OpenAPI transition catalog fields drifted.")
    if catalog["contractId"] != "s7-s8-openapi-transition.v1":
        raise ContractValidationError("S7/S8 OpenAPI transition contract ID drifted.")
    paths = _string_list(catalog["allowedPaths"], "S7/S8 allowedPaths")
    schemas = _string_list(catalog["allowedSchemaNames"], "S7/S8 allowedSchemaNames")
    if tuple(sorted(paths)) != paths or tuple(sorted(schemas)) != schemas:
        raise ContractValidationError("S7/S8 OpenAPI allowlists must be sorted.")
    digest = catalog["additiveProjectionSha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ContractValidationError("S7/S8 additive projection digest is invalid.")
    return catalog


def _remove_s7_s8_additive_openapi(
    document: dict[str, Any], catalog: Mapping[str, Any]
) -> None:
    paths = _object(document.get("paths"), "OpenAPI paths")
    schemas = _object(
        _object(document.get("components"), "OpenAPI components").get("schemas"),
        "OpenAPI component schemas",
    )
    allowed_paths = _string_list(catalog["allowedPaths"], "S7/S8 allowedPaths")
    allowed_schemas = _string_list(catalog["allowedSchemaNames"], "S7/S8 allowedSchemaNames")
    if any(path not in paths for path in allowed_paths) or any(name not in schemas for name in allowed_schemas):
        raise ContractValidationError("S7/S8 additive OpenAPI fragment is incomplete.")
    fragment = {
        "paths": {path: paths[path] for path in allowed_paths},
        "schemas": {name: schemas[name] for name in allowed_schemas},
    }
    actual = hashlib.sha256(canonical_json_bytes(fragment)).hexdigest()
    if actual != catalog["additiveProjectionSha256"]:
        raise ContractValidationError("S7/S8 additive OpenAPI fragment drifted.")
    for path in allowed_paths:
        paths.pop(path)
    for name in allowed_schemas:
        schemas.pop(name)


def _remove_allowed_root_tags(document: dict[str, Any], allowed: set[str]) -> None:
    tags = document.get("tags")
    if tags is None:
        return
    if not isinstance(tags, list):
        raise ContractValidationError("OpenAPI root tags must be an array.")
    retained: list[object] = []
    for entry in tags:
        if isinstance(entry, Mapping) and entry.get("name") in allowed:
            if set(entry) - {"name", "description"}:
                raise ContractValidationError(
                    "Signal v2 OpenAPI root tag fields drifted."
                )
            continue
        retained.append(entry)
    if retained:
        document["tags"] = retained
    else:
        document.pop("tags", None)


def project_preserved_openapi(
    document: Mapping[str, Any], catalog: Mapping[str, Any]
) -> dict[str, Any]:
    """허용된 S5 path/schema/tag를 제거해 historical preserved projection을 만든다."""

    projected = copy.deepcopy(_object(dict(document), "OpenAPI document"))
    paths = _object(projected.get("paths"), "OpenAPI paths")
    allowed_path = str(catalog["allowedPath"])
    if allowed_path in paths:
        path_item = _object(paths[allowed_path], "Signal v2 path item")
        methods = {
            key
            for key in path_item
            if key.lower()
            in {"get", "put", "post", "delete", "patch", "head", "options", "trace"}
        }
        if methods != set(catalog["allowedMethods"]):
            raise ContractValidationError("Signal v2 OpenAPI method set drifted.")
        if set(path_item) - methods - {"parameters", "summary", "description", "$ref"}:
            raise ContractValidationError(
                "Signal v2 OpenAPI path item contains an unapproved field."
            )
        paths.pop(allowed_path)

    components = _object(projected.get("components"), "OpenAPI components")
    schemas = _object(components.get("schemas"), "OpenAPI component schemas")
    for name in _string_list(catalog["allowedSchemaNames"], "allowedSchemaNames"):
        schemas.pop(name, None)
    _remove_allowed_root_tags(
        projected, set(_string_list(catalog["allowedRootTags"], "allowedRootTags"))
    )
    return projected


def verify_openapi_transition(
    path: Path = OPENAPI_PATH,
    catalog_path: Path = CATALOG_PATH,
    s7_s8_catalog_path: Path = S7_S8_CATALOG_PATH,
) -> None:
    """current OpenAPI가 historical 의미를 보존하고 exact Signal v2 route만 추가했는지 검증한다."""

    catalog = load_transition_catalog(catalog_path)
    if not path.is_file() or path.is_symlink():
        raise ContractValidationError("current OpenAPI is unavailable or unsafe.")
    raw = path.read_bytes()
    document = _object(
        load_json_bytes_strict(raw, source=str(path)), "OpenAPI document"
    )
    additive_catalog = load_s7_s8_transition_catalog(s7_s8_catalog_path)
    _remove_s7_s8_additive_openapi(document, additive_catalog)
    projected = project_preserved_openapi(document, catalog)
    actual = hashlib.sha256(canonical_json_bytes(projected)).hexdigest()
    if actual != catalog["historicalPreservedProjectionSha256"]:
        raise ContractValidationError(
            "current OpenAPI changed outside the approved Signal v2 transition."
        )


def historical_openapi_is_current(
    path: Path = OPENAPI_PATH, catalog_path: Path = CATALOG_PATH
) -> bool:
    """route 게시 전 historical raw OpenAPI bytes인지 반환한다."""

    catalog = load_transition_catalog(catalog_path)
    return (
        hashlib.sha256(path.read_bytes()).hexdigest()
        == catalog["historicalOpenApiRawSha256"]
    )
