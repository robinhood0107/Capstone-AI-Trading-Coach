from __future__ import annotations

import hashlib
import json
import math
from typing import TypeAlias, cast

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def canonical_json_bytes(value: object) -> bytes:
    """snapshot/manifest를 결정적 UTF-8 JSON과 마지막 newline을 포함한 bytes로 만든다."""
    normalized = _normalize(value, ancestors=set())
    return (
        json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def canonical_json_sha256(value: object) -> str:
    """consumer가 검증할 exact canonical bytes의 lowercase SHA-256을 반환한다."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _normalize(value: object, *, ancestors: set[int]) -> JsonValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON numbers must be finite")
        # IEEE negative zero는 의미 차이가 없으므로 서로 다른 artifact hash를 만들지 않게 0으로 고정한다.
        return 0 if value == 0 else value
    if isinstance(value, list):
        return cast(
            list[JsonValue], _normalize_container(value, ancestors=ancestors, is_mapping=False)
        )
    if isinstance(value, dict):
        return cast(
            dict[str, JsonValue], _normalize_container(value, ancestors=ancestors, is_mapping=True)
        )
    raise TypeError(f"canonical JSON does not support {type(value).__name__}")


def _normalize_container(
    value: list[object] | dict[object, object],
    *,
    ancestors: set[int],
    is_mapping: bool,
) -> list[JsonValue] | dict[str, JsonValue]:
    identity = id(value)
    if identity in ancestors:
        raise ValueError("canonical JSON does not allow cyclic containers")
    ancestors.add(identity)
    try:
        if is_mapping:
            mapping = cast(dict[object, object], value)
            if any(not isinstance(key, str) for key in mapping):
                raise TypeError("canonical JSON object key must be a string")
            return {
                cast(str, key): _normalize(child, ancestors=ancestors)
                for key, child in mapping.items()
            }
        sequence = cast(list[object], value)
        return [_normalize(child, ancestors=ancestors) for child in sequence]
    finally:
        ancestors.remove(identity)
