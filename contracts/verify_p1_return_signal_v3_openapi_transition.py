"""Verify the additive exact-75 -> exact-76 confidence-free Signal v3 transition."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.generate_principle_contracts import (  # noqa: E402
    ContractValidationError,
    canonical_json_bytes,
)
from contracts.historical_openapi_projection import (  # noqa: E402
    project_historical_root,
)
from contracts.verify_p1_v3_automation_openapi_transition import operations  # noqa: E402

OPENAPI_PATH = ROOT / "contracts/openapi/openapi.json"
ADDITIVE_PATH = ROOT / "contracts/openapi/p1-return-signal-v3.v1.openapi.json"
HISTORICAL_ROOT_75_SHA256 = (
    "b83a304de5637661bd00c05cdae873aab9ec9232e618d333be6708f5595a1b52"
)
SIGNAL_V3_SCHEMAS = (
    "SignalV3AbstainComponent",
    "SignalV3PredictiveComponent",
    "SignalV3RegimeComponent",
    "SignalV3RuntimeComponentResponse",
    "SignalV3RuntimeComponentsResponse",
    "SignalV3RuntimeCompositeResponse",
    "SignalV3RuntimeResponse",
    "SignalV3RuntimeSuccessResponse",
)


def _historical_digest(document: Mapping[str, Any]) -> str:
    """동결 세대의 바이트로 되돌린 뒤 해시한다.

    되돌리기를 **해시 비교에서만** 쓴다. project_pre_signal_v3 의 반환값에 적용하면
    그 값을 받아 v3 세대를 검증하는 쪽이 깨진다 - 그쪽은 현재 바이트를 봐야 한다.
    """

    return hashlib.sha256(
        canonical_json_bytes(project_historical_root(document))
    ).hexdigest()


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractValidationError(f"{label} must be an object")
    return value


def project_pre_signal_v3(current: Mapping[str, Any]) -> dict[str, Any]:
    from contracts.generate_owner_ridge_contracts import project_previous

    current = project_previous(current)
    if len(operations(current)) != 76:
        raise ContractValidationError("Signal v3 projection requires exact-76 root")
    projected = copy.deepcopy(current)
    paths = _object(projected.get("paths"), "paths")
    item = _object(paths.get("/api/v3/signals/{symbol}"), "Signal v3 path")
    operation = _object(item.get("get"), "Signal v3 GET")
    if operation.get("operationId") != "readSignalV3":
        raise ContractValidationError("Signal v3 operation drifted")
    paths.pop("/api/v3/signals/{symbol}")
    schemas = _object(
        _object(projected.get("components"), "components").get("schemas"), "schemas"
    )
    for name in SIGNAL_V3_SCHEMAS:
        if name not in schemas:
            raise ContractValidationError(f"Signal v3 schema missing: {name}")
        schemas.pop(name)
    if len(operations(projected)) != 75:
        raise ContractValidationError(
            "Signal v3 projection did not restore exact-75 root"
        )
    if _historical_digest(projected) != HISTORICAL_ROOT_75_SHA256:
        raise ContractValidationError(
            "Signal v3 changed root bytes outside its additive surface"
        )
    return projected


def verify_transition(path: Path = OPENAPI_PATH) -> None:
    current = _object(json.loads(path.read_text(encoding="utf-8")), "root OpenAPI")
    from contracts.generate_owner_ridge_contracts import project_previous

    current = project_previous(current)
    if len(operations(current)) == 75:
        if _historical_digest(current) != HISTORICAL_ROOT_75_SHA256:
            raise ContractValidationError("historical exact-75 root drifted")
        return
    project_pre_signal_v3(current)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--openapi", type=Path, default=OPENAPI_PATH)
    args = parser.parse_args()
    try:
        verify_transition(args.openapi)
    except (ContractValidationError, OSError, json.JSONDecodeError) as error:
        print(f"P1_RETURN_SIGNAL_V3_OPENAPI_TRANSITION=FAIL: {error}")
        return 1
    print("P1_RETURN_SIGNAL_V3_OPENAPI_TRANSITION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
