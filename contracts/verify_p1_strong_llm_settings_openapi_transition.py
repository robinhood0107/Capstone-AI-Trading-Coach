"""Verify the exact additive root OpenAPI transition from 68 to 69 operations."""

from __future__ import annotations

import argparse
import copy
import hashlib
from pathlib import Path
import sys
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
ADDITIVE_PATH: Final = ROOT / "contracts/openapi/p1-strong-llm-settings.v1.openapi.json"
# 이 전이가 더하는 것은 operation 하나뿐이다. 설정 읽기는 `RagV2CorpusStatus`에 실리는데,
# 그 스키마는 다음 단계(68->61)가 통째로 걷어내므로 필드를 늘려도 사슬을 건드리지 않는다.
# 여기에 새 component schema를 만들면 그 투영에 남아 exact-61 해시가 깨진다.
#
# 그래서 이 층에는 자기 byte anchor가 없다. 68 문서는 RagV2CorpusStatus가 자란 만큼 달라지고,
# 그 차이는 다음 층이 그 스키마를 걷어낼 때 사라진다. 이 층이 지키는 것은 두 가지다 -
# operation이 정확히 하나 늘었고, 그것을 걷어낸 문서가 여전히 동결된 exact-61로 수렴한다.
HTTP_METHODS: Final = frozenset(
    {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
)
ADDITIVE_SCHEMA_NAMES: Final = frozenset()


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


def strip_strong_llm_settings(
    current: Mapping[str, Any],
    additive: Mapping[str, Any],
) -> dict[str, Any]:
    """승인된 operation 하나만 걷어낸다. 해시 대조는 하지 않는다.

    다음 층이 자기 투영 안에서 이 함수를 부른다. 거기서 다시 해시를 대조하면 같은 검사가
    두 번 돌고, 그 두 번째는 이 층이 이미 통과시킨 문서를 대상으로 한다.
    """

    current_operations = operations(current)
    additive_operations = operations(additive)
    if len(additive_operations) != 1:
        raise ContractValidationError(
            "Strong LLM settings additive OpenAPI must contain exact one operation."
        )
    for identity, operation_id in additive_operations.items():
        if current_operations.get(identity) != operation_id:
            raise ContractValidationError(
                f"Strong LLM settings operation drifted: {identity[1].upper()} {identity[0]}."
            )
    projected = copy.deepcopy(current)
    projected_paths = _object(projected.get("paths"), "projected OpenAPI paths")
    for path_name, method in additive_operations:
        path_item = _object(projected_paths.get(path_name), f"path item {path_name}")
        path_item.pop(method)
        if not any(key in HTTP_METHODS for key in path_item):
            projected_paths.pop(path_name)
    return projected


def project_pre_strong_llm_openapi(
    current: Mapping[str, Any],
    additive: Mapping[str, Any],
) -> dict[str, Any]:
    """승인된 Strong LLM 설정 표면을 걷어내고 byte-stable exact-68을 복원한다."""

    current_operations = operations(current)
    additive_operations = operations(additive)
    if len(current_operations) != 69 or len(set(current_operations.values())) != 69:
        raise ContractValidationError(
            "current root OpenAPI must contain exact 69 unique operations."
        )
    if len(additive_operations) != 1:
        raise ContractValidationError(
            "Strong LLM settings additive OpenAPI must contain exact one operation."
        )
    for identity, operation_id in additive_operations.items():
        if current_operations.get(identity) != operation_id:
            raise ContractValidationError(
                f"Strong LLM settings operation drifted: {identity[1].upper()} {identity[0]}."
            )

    projected = copy.deepcopy(current)
    projected_paths = _object(projected.get("paths"), "projected OpenAPI paths")
    for path_name, method in additive_operations:
        path_item = _object(projected_paths.get(path_name), f"path item {path_name}")
        path_item.pop(method)
        if not any(key in HTTP_METHODS for key in path_item):
            projected_paths.pop(path_name)
    if len(operations(projected)) != 68:
        raise ContractValidationError(
            "Strong LLM settings projection must restore exact 68 operations."
        )

    from contracts.verify_p1_rag_v2_openapi_transition import (
        ADDITIVE_PATH as RAG_V2_ADDITIVE_PATH,
        HISTORICAL_ROOT_61_SHA256,
        project_pre_rag_v2_openapi,
    )

    _, rag_v2_additive = _load(RAG_V2_ADDITIVE_PATH, "RAG v2 additive OpenAPI")
    restored = project_pre_rag_v2_openapi(projected, rag_v2_additive)
    actual = hashlib.sha256(canonical_json_bytes(restored)).hexdigest()
    if actual != HISTORICAL_ROOT_61_SHA256:
        raise ContractValidationError(
            "current root OpenAPI changed outside the approved Strong LLM settings addition."
        )
    return projected


def verify_transition(
    path: Path = OPENAPI_PATH,
    additive_path: Path = ADDITIVE_PATH,
) -> None:
    _, current = _load(path, "current root OpenAPI")
    if len(operations(current)) == 68:
        # 이 표면이 아직 더해지지 않은 과거 문서다. 그때의 검증은 다음 층이 이미 한다.
        return
    _, additive = _load(additive_path, "Strong LLM settings additive OpenAPI")
    project_pre_strong_llm_openapi(current, additive)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the exact Strong LLM settings root OpenAPI transition."
    )
    parser.add_argument("--openapi", type=Path, default=OPENAPI_PATH)
    parser.add_argument("--additive", type=Path, default=ADDITIVE_PATH)
    arguments = parser.parse_args()
    try:
        verify_transition(arguments.openapi, arguments.additive)
    except (ContractValidationError, OSError) as error:
        print(f"Strong LLM settings OpenAPI transition failed: {error}")
        return 1
    print("P1_STRONG_LLM_SETTINGS_OPENAPI_TRANSITION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
