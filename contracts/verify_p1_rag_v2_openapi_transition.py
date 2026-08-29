"""Verify the exact additive root OpenAPI transition from 61 to 68 operations.

RAG v2의 공개 표면 일곱 개가 더해질 때, 그 일곱 개와 그것이 데려온 스키마만 빼면 역사적
exact-61 바이트가 그대로 복원되는지 본다. 동결의 정체는 바이트 고정이 아니라 projection
복원 가능성이므로, 이 단계를 68 → 61 → 56 → 48 체인의 맨 앞에 끼운다.
"""

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

ROOT: Final = _SCRIPT_ROOT
OPENAPI_PATH: Final = ROOT / "contracts/openapi/openapi.json"
ADDITIVE_PATH: Final = ROOT / "contracts/openapi/p1-rag-v2-public.v1.openapi.json"
HISTORICAL_ROOT_61_SHA256: Final = (
    "e4c14ca47c3f2b3891c8b898562ef851bbcdaae85fd0b99df4504c2ec459d674"
)
HTTP_METHODS: Final = frozenset(
    {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
)
ADDITIVE_SCHEMA_NAMES: Final = frozenset(
    {
        "RagV2Answer",
        "RagV2CorpusStatus",
        "RagV2EffectiveConsent",
        "RagV2HistoryDetail",
        "RagV2HistoryMetadata",
        "RagV2HistoryPage",
    }
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


def operations(document: Mapping[str, Any]) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    ids: set[str] = set()
    for path, raw_item in _object(document.get("paths"), "OpenAPI paths").items():
        item = _object(raw_item, f"path item {path}")
        for method, raw_operation in item.items():
            if method not in HTTP_METHODS:
                continue
            operation = _object(raw_operation, f"operation {method} {path}")
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str) or not operation_id or operation_id in ids:
                raise ContractValidationError("OpenAPI operationId is missing or duplicated.")
            ids.add(operation_id)
            result[(path, method)] = operation_id
    return result


def project_pre_rag_v2_openapi(
    current: Mapping[str, Any],
    additive: Mapping[str, Any],
) -> dict[str, Any]:
    """승인된 RAG v2 공개 표면을 제거하고 바이트 안정한 exact-61 root를 복원한다."""

    current_operations = operations(current)
    additive_operations = operations(additive)
    if len(current_operations) != 68 or len(set(current_operations.values())) != 68:
        raise ContractValidationError(
            "current root OpenAPI must contain exact 68 unique operations."
        )
    if len(additive_operations) != 7 or len(set(additive_operations.values())) != 7:
        raise ContractValidationError("RAG v2 additive OpenAPI must contain exact seven operations.")
    for identity, operation_id in additive_operations.items():
        if current_operations.get(identity) != operation_id:
            raise ContractValidationError(
                f"RAG v2 additive operation drifted: {identity[1].upper()} {identity[0]}."
            )

    projected = copy.deepcopy(current)
    projected_paths = _object(projected.get("paths"), "projected OpenAPI paths")
    for path_name, method in additive_operations:
        path_item = _object(projected_paths.get(path_name), f"path item {path_name}")
        path_item.pop(method)
        if not any(key in HTTP_METHODS for key in path_item):
            projected_paths.pop(path_name)
    if len(operations(projected)) != 61:
        raise ContractValidationError("RAG v2 projection must restore exact 61 operations.")

    schemas = _object(
        _object(projected.get("components"), "projected components").get("schemas"),
        "projected schemas",
    )
    missing = ADDITIVE_SCHEMA_NAMES - set(schemas)
    if missing:
        raise ContractValidationError(
            "RAG v2 additive schema set is incomplete: " + ", ".join(sorted(missing))
        )
    for name in ADDITIVE_SCHEMA_NAMES:
        schemas.pop(name)

    actual = hashlib.sha256(canonical_json_bytes(projected)).hexdigest()
    if actual != HISTORICAL_ROOT_61_SHA256:
        raise ContractValidationError(
            "current root OpenAPI changed outside the approved exact-seven RAG v2 addition."
        )
    return projected


def verify_transition(
    path: Path = OPENAPI_PATH,
    additive_path: Path = ADDITIVE_PATH,
) -> None:
    raw, current = _load(path, "current root OpenAPI")
    if hashlib.sha256(raw).hexdigest() == HISTORICAL_ROOT_61_SHA256:
        return
    _, additive = _load(additive_path, "RAG v2 additive OpenAPI")
    project_pre_rag_v2_openapi(current, additive)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the exact RAG v2 OpenAPI transition.")
    parser.add_argument("--openapi", type=Path, default=OPENAPI_PATH)
    parser.add_argument("--additive", type=Path, default=ADDITIVE_PATH)
    arguments = parser.parse_args()
    try:
        verify_transition(arguments.openapi, arguments.additive)
    except (ContractValidationError, OSError) as error:
        print(f"P1 RAG v2 OpenAPI transition failed: {error}")
        return 1
    print("P1_RAG_V2_OPENAPI_TRANSITION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
