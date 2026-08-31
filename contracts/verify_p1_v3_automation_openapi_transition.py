"""Verify the exact additive OpenAPI transition from current exact-69 to V3 exact-75."""

from __future__ import annotations

import argparse
import copy
import hashlib
import sys
from pathlib import Path
from typing import Any, Final, Mapping

ROOT: Final = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.generate_principle_contracts import (  # noqa: E402
    ContractValidationError,
    canonical_json_bytes,
    load_json_bytes_strict,
)

OPENAPI_PATH: Final = ROOT / "contracts/openapi/openapi.json"
ADDITIVE_PATH: Final = ROOT / "contracts/openapi/p1-automation-v3.v1.openapi.json"
CURRENT_ROOT_69_SHA256: Final = (
    "8fe3ba8adc11d8702e7f06195deeedc70f11fff47b7f503fa83d15a98a04ebd7"
)
HTTP_METHODS: Final = frozenset(
    {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
)


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractValidationError(f"{label} must be an object.")
    return value


def operations(document: Mapping[str, Any]) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    identifiers: set[str] = set()
    for path, raw_item in _object(document.get("paths"), "OpenAPI paths").items():
        item = _object(raw_item, f"path item {path}")
        for method, raw_operation in item.items():
            if method not in HTTP_METHODS:
                continue
            operation = _object(raw_operation, f"operation {method} {path}")
            operation_id = operation.get("operationId")
            if (
                not isinstance(operation_id, str)
                or not operation_id
                or operation_id in identifiers
            ):
                raise ContractValidationError(
                    "OpenAPI operationId is missing or duplicated."
                )
            identifiers.add(operation_id)
            result[(path, method)] = operation_id
    return result


def merge_v3_openapi(
    current: Mapping[str, Any], additive: Mapping[str, Any]
) -> dict[str, Any]:
    current_ops = operations(current)
    additive_ops = operations(additive)
    if len(current_ops) != 69 or len(additive_ops) != 6:
        raise ContractValidationError("V3 merge requires exact-69 plus exact-six.")
    if set(current_ops).intersection(additive_ops) or set(
        current_ops.values()
    ).intersection(additive_ops.values()):
        raise ContractValidationError(
            "V3 additive operations collide with the current root."
        )
    merged = copy.deepcopy(current)
    paths = _object(merged.get("paths"), "merged paths")
    for path, item in _object(additive.get("paths"), "additive paths").items():
        paths[path] = copy.deepcopy(item)
    components = _object(
        _object(merged.get("components"), "components").get("schemas"), "schemas"
    )
    additive_components = _object(
        _object(additive.get("components"), "additive components").get("schemas"),
        "additive schemas",
    )
    collision = set(components).intersection(additive_components)
    if collision:
        raise ContractValidationError(
            "V3 additive schema names collide: " + ", ".join(sorted(collision))
        )
    components.update(copy.deepcopy(additive_components))
    if len(operations(merged)) != 75:
        raise ContractValidationError(
            "V3 merged root must contain exact 75 operations."
        )
    return merged


def project_pre_v3_openapi(
    current: Mapping[str, Any], additive: Mapping[str, Any]
) -> dict[str, Any]:
    current_ops = operations(current)
    additive_ops = operations(additive)
    if len(current_ops) != 75 or len(additive_ops) != 6:
        raise ContractValidationError("V3 projection requires exact-75 and exact-six.")
    projected = copy.deepcopy(current)
    paths = _object(projected.get("paths"), "projected paths")
    for path, method in additive_ops:
        item = _object(paths.get(path), f"path item {path}")
        if item.get(method, {}).get("operationId") != additive_ops[(path, method)]:
            raise ContractValidationError(
                "V3 additive operation drifted before projection."
            )
        item.pop(method)
        if not any(key in HTTP_METHODS for key in item):
            paths.pop(path)
    schemas = _object(
        _object(projected.get("components"), "components").get("schemas"), "schemas"
    )
    for name in _object(
        _object(additive.get("components"), "additive components").get("schemas"),
        "additive schemas",
    ):
        if name not in schemas:
            raise ContractValidationError(
                "V3 additive schema is missing before projection."
            )
        schemas.pop(name)
    if len(operations(projected)) != 69:
        raise ContractValidationError("V3 projection must restore exact 69 operations.")
    if (
        hashlib.sha256(canonical_json_bytes(projected)).hexdigest()
        != CURRENT_ROOT_69_SHA256
    ):
        raise ContractValidationError(
            "V3 projection changed current root bytes outside the overlay."
        )
    return projected


def _load(path: Path) -> dict[str, Any]:
    value = load_json_bytes_strict(path.read_bytes(), source=str(path))
    return _object(value, str(path))


def verify_transition(
    openapi_path: Path = OPENAPI_PATH, additive_path: Path = ADDITIVE_PATH
) -> None:
    current = _load(openapi_path)
    additive = _load(additive_path)
    count = len(operations(current))
    if count == 69:
        merged = merge_v3_openapi(current, additive)
        project_pre_v3_openapi(merged, additive)
        return
    if count == 75:
        project_pre_v3_openapi(current, additive)
        return
    raise ContractValidationError(
        "root OpenAPI is neither pre-V3 exact-69 nor V3 exact-75."
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--openapi", type=Path, default=OPENAPI_PATH)
    parser.add_argument("--additive", type=Path, default=ADDITIVE_PATH)
    arguments = parser.parse_args()
    try:
        verify_transition(arguments.openapi, arguments.additive)
    except (ContractValidationError, OSError) as error:
        print(f"P1_V3_AUTOMATION_OPENAPI_TRANSITION=FAIL: {error}")
        return 1
    print("P1_V3_AUTOMATION_OPENAPI_TRANSITION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
