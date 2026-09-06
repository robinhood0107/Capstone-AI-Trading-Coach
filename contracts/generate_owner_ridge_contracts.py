"""Owner 중지와 Ridge 신호의 additive overlay. 이전 exact-76 문서는 원형 보존한다."""

from __future__ import annotations
from contracts.generate_principle_contracts import ContractValidationError

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS = ROOT / "contracts/openapi/p1-owner-ridge.previous.openapi.json"
OVERLAY = ROOT / "contracts/openapi/p1-owner-ridge.v1.openapi.json"


def build():
    previous = json.loads(PREVIOUS.read_text())
    schemas = previous["components"]["schemas"]
    forecast = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "horizonSessions": {"type": "integer", "enum": [1, 5, 20]},
            "targetSession": {"type": "string", "format": "date"},
            "expectedReturn": {"type": "number"},
            "forecastClose": {"type": "number", "exclusiveMinimum": 0},
            "trainSamples": {"type": "integer", "minimum": 40},
            "trainedThrough": {"type": "string", "format": "date"},
        },
        "required": [
            "horizonSessions",
            "targetSession",
            "expectedReturn",
            "forecastClose",
            "trainSamples",
            "trainedThrough",
        ],
    }
    predictive = copy.deepcopy(schemas["SignalV3PredictiveComponent"])
    predictive["oneOf"][0]["properties"].update(
        {
            "returnForecasts": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/RuntimeReturnForecast"},
                "minItems": 3,
                "maxItems": 3,
            },
            "estimator": {"type": "string", "enum": ["RIDGE"]},
            "sourceSession": {"type": "string", "format": "date"},
            "qualityStatus": {"type": "string", "enum": ["COMPARISON_PENDING"]},
        }
    )
    runtime = copy.deepcopy(schemas["SignalV3RuntimeComponentResponse"])
    runtime["properties"].update({
        "returnForecasts": {"type": ["array", "null"], "items": {"$ref": "#/components/schemas/RuntimeReturnForecast"}},
        "estimator": {"type": ["string", "null"]},
        "sourceSession": {"type": ["string", "null"], "format": "date"},
        "qualityStatus": {"type": ["string", "null"]},
    })
    status = copy.deepcopy(schemas["AutomationStatusV3"])
    status["properties"].update(
        {
            "appliedPolicyVersion": {"type": ["integer", "null"], "minimum": 1},
            "policyRecoverySourceVersion": {"type": ["integer", "null"], "minimum": 1},
            "nextRunAt": {"type": ["string", "null"], "format": "date-time"},
        }
    )
    owner = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "active": {"type": "boolean"},
            "globalActive": {"type": "boolean"},
            "effectiveActive": {"type": "boolean"},
            "changedAt": {"type": "string", "format": "date-time"},
            "reasonClass": {
                "type": "string",
                "enum": ["INITIAL_STATE", "USER_MANUAL_STOP", "USER_RESUME"],
            },
        },
        "required": [
            "active",
            "globalActive",
            "effectiveActive",
            "changedAt",
            "reasonClass",
        ],
    }
    envelope = copy.deepcopy(schemas["S24KillSwitchSuccessResponse"])
    envelope["properties"]["data"] = {"$ref": "#/components/schemas/OwnerKillSwitchDto"}
    global_path = copy.deepcopy(previous["paths"]["/api/v1/risk/kill-switch"])
    owner_path = copy.deepcopy(global_path)
    for method, name in [
        ("get", "readOwnerKillSwitch"),
        ("post", "changeOwnerKillSwitch"),
    ]:
        owner_path[method]["operationId"] = name
        owner_path[method]["summary"] = "본인 주문 중지 " + (
            "조회" if method == "get" else "변경"
        )
        owner_path[method]["description"] = (
            "인증된 본인만 제어한다. 전역 중지는 해제하지 않으며 해제 후 자동 재무장하지 않는다."
        )
        owner_path[method]["responses"]["200"]["content"]["application/json"][
            "schema"
        ] = {"$ref": "#/components/schemas/ApiResponseOwnerKillSwitchDto"}
        global_path[method]["description"] = (
            "관리자 전용 전역 비상정지. 조회·정지·해제 모두 현재 ADMIN 권한을 검증한다."
        )
        global_path[method]["x-required-role"] = "ADMIN"
    forecast["required"].sort()
    owner["required"].sort()
    return {
        "openapi": "3.1.0",
        "info": {"title": "Owner stop and Ridge return overlay", "version": "1.0.0"},
        "paths": {
            "/api/v1/risk/kill-switch": global_path,
            "/api/v2/risk/kill-switch": owner_path,
        },
        "components": {
            "schemas": {
                "RuntimeReturnForecast": forecast,
                "SignalV3PredictiveComponent": predictive,
                "SignalV3RuntimeComponentResponse": runtime,
                "AutomationStatusV3": status,
                "OwnerKillSwitchDto": owner,
                "ApiResponseOwnerKillSwitchDto": envelope,
            }
        },
    }


def project_previous(document):
    """허용한 surface만 복원해 historical verifier가 이전 계약을 계속 검증한다."""
    if "/api/v2/risk/kill-switch" not in document.get("paths", {}):
        return document
    previous = json.loads(PREVIOUS.read_text())
    overlay = build()
    for path, value in overlay["paths"].items():
        if document["paths"].get(path) != value:
            raise ContractValidationError(f"owner/Ridge path drift: {path}")
    for name, value in overlay["components"]["schemas"].items():
        if document["components"]["schemas"].get(name) != value:
            raise ContractValidationError(f"owner/Ridge schema drift: {name}")
    result = copy.deepcopy(document)
    for path in overlay["paths"]:
        if path in previous["paths"]:
            result["paths"][path] = previous["paths"][path]
        else:
            result["paths"].pop(path, None)
    for name in overlay["components"]["schemas"]:
        if name in previous["components"]["schemas"]:
            result["components"]["schemas"][name] = previous["components"]["schemas"][
                name
            ]
        else:
            result["components"]["schemas"].pop(name, None)
    return result


if __name__ == "__main__":
    OVERLAY.write_text(
        json.dumps(build(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
