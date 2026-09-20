"""동결된 세대의 바이트로 되돌린다.

왜 있나
-------
세대 검증기들은 "지금 문서를 한 세대 되돌리면 그때 바이트와 같다"를 확인한다. 그런데
그 확인은 **이후에 내린 제품 결정을 모른다.** 매수 마감이 09:40 에서 14:30 으로 늘어난
순간, 그 결정과 무관한 검증기 25건이 한꺼번에 깨졌다.

되돌릴 것을 한곳에 모은다. 새 결정이 생기면 여기 한 줄을 더하면 되고, 어느 검증기가
무엇을 모르는지 따로 외우지 않아도 된다.

무엇을 하지 않나
---------------
기대한 현재 값도 동결 값도 아니면 **조용히 넘어가지 않고 실패한다.** 새 드리프트가
생겼다는 뜻이고, 그것을 여기서 묻으면 동결 해시가 지키려던 것이 사라진다.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any, Final, Mapping

from contracts.generate_principle_contracts import (
    ContractValidationError,
    canonical_json_bytes,
)

#: (속성 이름, 칸, 지금 값, 동결 세대의 값).
#:
#: - buyCutoffTimeKst: 매수 마감을 09:40 에서 14:30 으로 늘렸다. 오전 한 번만 사던 것을
#:   하루 세 번 판단으로 바꾼 결정이다.
#: - expirySession: 만기 세션이 nullable 이 됐다. 만기가 없는 보유가 생겼기 때문이다.
_ATOMS: Final[tuple[tuple[str, str, Any, Any], ...]] = (
    ("buyCutoffTimeKst", "enum", ["14:30"], ["09:40"]),
    ("buyCutoffTimeKst", "const", "14:30", "09:40"),
    ("buyCutoffTimeKst", "default", "14:30", "09:40"),
    ("buyCutoffTimeKst", "example", "14:30", "09:40"),
    ("expirySession", "type", ["string", "null"], "string"),
)

#: 동결 해시가 찍힌 뒤 springdoc 이 찍는 버전이 내려갔다. 제품 결정이 아니라 생성기 변화다.
_ROOT_OPENAPI_VERSION: Final = "3.1.1"


def project_historical_fragment(node: Any) -> Any:
    """문서 조각 안의 원자를 동결 세대 값으로 되돌린 복사본을 만든다."""

    return _revert(copy.deepcopy(node))


def project_historical_root(document: Mapping[str, Any]) -> dict[str, Any]:
    """문서 전체를 되돌린다. 조각 되돌리기에 루트 버전까지 더한다."""

    projected: dict[str, Any] = project_historical_fragment(dict(document))
    if projected.get("openapi") in {"3.1.0", _ROOT_OPENAPI_VERSION}:
        projected["openapi"] = _ROOT_OPENAPI_VERSION
    return projected


def _revert(node: Any) -> Any:
    if isinstance(node, dict):
        for name, field, current_value, historical_value in _ATOMS:
            prop = node.get(name)
            if not isinstance(prop, dict) or field not in prop:
                continue
            if prop[field] == historical_value:
                continue
            if prop[field] != current_value:
                raise ContractValidationError(
                    f"unexpected drift at {name}.{field}: {prop[field]!r}"
                )
            prop[field] = historical_value
        for value in node.values():
            _revert(value)
    elif isinstance(node, list):
        for value in node:
            _revert(value)
    return node


def historical_digest(document: Mapping[str, Any]) -> str:
    """동결 세대의 바이트로 되돌린 뒤 해시한다.

    되돌리기는 **비교할 때만** 쓴다. 투영 함수의 반환값에 적용하면 그 값을 받아 현재
    세대를 검증하는 쪽이 깨진다 - 그쪽은 현재 바이트를 봐야 한다.
    """

    return hashlib.sha256(
        canonical_json_bytes(project_historical_root(document))
    ).hexdigest()
