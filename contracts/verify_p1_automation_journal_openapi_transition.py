"""Root OpenAPI의 exact-48 -> exact-56 Automation/Journal 전환만 허용한다."""

from __future__ import annotations

import argparse
import copy
import hashlib
import sys
from pathlib import Path
from typing import Any, Final, Mapping

_SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))

from contracts.generate_principle_contracts import (  # noqa: E402
    ContractValidationError,
    canonical_json_bytes,
    load_json_bytes_strict,
)
from contracts.verify_s5_signal_runtime_transition import verify_openapi_transition  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = ROOT / "contracts/openapi/openapi.json"
ADDITIVE_PATH = ROOT / "contracts/openapi/p1-automation-journal.v1.openapi.json"
HISTORICAL_ROOT_48_SHA256: Final = (
    "71d292c4c48655175a392c1723c818195e67459a569f732aaf91cadafe888448"
)
HTTP_METHODS: Final = frozenset(
    {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
)
ADDITIVE_SCHEMA_NAMES: Final = frozenset(
    {
        "ApiResponseAutomationControl",
        "ApiResponseAutomationRunPage",
        "ApiResponseJournal",
        "ApiResponseJournalPage",
        "ArmAutomationRequest",
        "AutomationControl",
        "AutomationRun",
        "AutomationRunPage",
        "CreateJournalRequest",
        "DeleteJournalRequest",
        "DisarmAutomationRequest",
        "Journal",
        "JournalLinks",
        "JournalPage",
        "JournalWriteLinks",
        "P1AutomationErrorResponse",
        "P1JournalErrorResponse",
        "ReplaceJournalRequest",
    }
)
CANONICAL_ADDITIVE_SCHEMAS: Final = frozenset(
    {"AutomationControl", "AutomationRun", "Journal"}
)


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractValidationError(f"{label} must be an object.")
    return value


def _load(path: Path, label: str) -> tuple[bytes, dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        raise ContractValidationError(f"{label} is unavailable or unsafe.")
    raw = path.read_bytes()
    document = _object(load_json_bytes_strict(raw, source=str(path)), label)
    if raw != canonical_json_bytes(document):
        raise ContractValidationError(f"{label} must use canonical JSON bytes.")
    return raw, document


def _operations(document: Mapping[str, Any]) -> dict[tuple[str, str], str]:
    paths = _object(document.get("paths"), "OpenAPI paths")
    operations: dict[tuple[str, str], str] = {}
    for path, raw_item in paths.items():
        item = _object(raw_item, f"path item {path}")
        for method, raw_operation in item.items():
            if method not in HTTP_METHODS:
                continue
            operation = _object(raw_operation, f"operation {method} {path}")
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str) or not operation_id:
                raise ContractValidationError(
                    f"operationId is missing for {method.upper()} {path}."
                )
            operations[(path, method)] = operation_id
    return operations


def _semantic_schema(value: object) -> object:
    if isinstance(value, dict):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if key == "required" and item == []:
                continue
            if key == "required" and isinstance(item, list):
                normalized[key] = sorted(item)
            else:
                normalized[key] = _semantic_schema(item)
        return normalized
    if isinstance(value, list):
        return [_semantic_schema(item) for item in value]
    return value


def project_pre_p1_openapi(
    current: Mapping[str, Any],
    additive: Mapping[str, Any],
) -> dict[str, Any]:
    """exact P1 additive fragment를 제거하고 검증된 exact-48 projection을 반환한다."""

    current_operations = _operations(current)
    if len(current_operations) == 76:
        from contracts.verify_p1_return_signal_v3_openapi_transition import (
            project_pre_signal_v3,
        )

        current = project_pre_signal_v3(current)
        current_operations = _operations(current)
    if len(current_operations) == 75:
        from contracts.verify_p1_v3_automation_openapi_transition import (
            ADDITIVE_PATH as V3_ADDITIVE_PATH,
            project_pre_v3_openapi,
        )

        _, v3_additive = _load(V3_ADDITIVE_PATH, "Automation V3 additive OpenAPI")
        current = project_pre_v3_openapi(current, v3_additive)
        current_operations = _operations(current)
    if len(current_operations) in {61, 68, 69}:
        from contracts.verify_p1_v91_automation_openapi_transition import (
            ADDITIVE_PATH as V91_ADDITIVE_PATH,
            project_pre_v91_openapi,
        )

        _, v91_additive = _load(V91_ADDITIVE_PATH, "V91 additive OpenAPI")
        current = project_pre_v91_openapi(current, v91_additive)
        current_operations = _operations(current)
    additive_operations = _operations(additive)
    if len(current_operations) != 56 or len(set(current_operations.values())) != 56:
        raise ContractValidationError(
            "current root OpenAPI must contain exact 56 unique operations."
        )
    if len(additive_operations) != 8 or len(set(additive_operations.values())) != 8:
        raise ContractValidationError("P1 additive OpenAPI must contain exact eight operations.")
    for identity, operation_id in additive_operations.items():
        if current_operations.get(identity) != operation_id:
            raise ContractValidationError(
                f"P1 additive operation drifted: {identity[1].upper()} {identity[0]}."
            )

    projected = copy.deepcopy(current)
    projected_paths = _object(projected.get("paths"), "projected OpenAPI paths")
    for path_name, method in additive_operations:
        path_item = _object(projected_paths.get(path_name), f"path item {path_name}")
        path_item.pop(method)
        if not any(key in HTTP_METHODS for key in path_item):
            projected_paths.pop(path_name)
    if len(_operations(projected)) != 48:
        raise ContractValidationError("P1 transition projection must restore exact 48 operations.")

    current_schemas = _object(
        _object(current.get("components"), "current components").get("schemas"),
        "current schemas",
    )
    additive_schemas = _object(
        _object(additive.get("components"), "additive components").get("schemas"),
        "additive schemas",
    )
    for name in CANONICAL_ADDITIVE_SCHEMAS:
        if _semantic_schema(current_schemas.get(name)) != _semantic_schema(
            additive_schemas.get(name)
        ):
            raise ContractValidationError(f"canonical additive schema drifted: {name}.")

    projected_schemas = _object(
        _object(projected.get("components"), "projected components").get("schemas"),
        "projected schemas",
    )
    missing = ADDITIVE_SCHEMA_NAMES - set(projected_schemas)
    if missing:
        raise ContractValidationError("P1 additive schema set is incomplete.")
    for name in ADDITIVE_SCHEMA_NAMES:
        projected_schemas.pop(name)

    projected_hash = hashlib.sha256(canonical_json_bytes(projected)).hexdigest()
    if projected_hash != HISTORICAL_ROOT_48_SHA256:
        raise ContractValidationError(
            "current root OpenAPI changed outside the exact Automation/Journal addition."
        )
    return projected


def verify_transition(
    path: Path = OPENAPI_PATH,
    additive_path: Path = ADDITIVE_PATH,
) -> None:
    raw, current = _load(path, "current root OpenAPI")
    if hashlib.sha256(raw).hexdigest() == HISTORICAL_ROOT_48_SHA256:
        verify_openapi_transition(path)
        return

    _, additive = _load(additive_path, "P1 additive OpenAPI")
    project_pre_p1_openapi(current, additive)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the exact P1 Automation/Journal root OpenAPI transition."
    )
    parser.add_argument("--openapi", type=Path, default=OPENAPI_PATH)
    parser.add_argument("--additive", type=Path, default=ADDITIVE_PATH)
    arguments = parser.parse_args()
    try:
        verify_transition(arguments.openapi, arguments.additive)
    except (ContractValidationError, OSError) as error:
        print(f"P1 Automation/Journal OpenAPI transition failed: {error}")
        return 1
    print("P1_AUTOMATION_JOURNAL_OPENAPI_TRANSITION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
