"""Project the approved V219 S3.2 order-status and reconciliation additions away."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

from contracts.generate_principle_contracts import ContractValidationError

ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = ROOT / "contracts/openapi/openapi.json"
BASE_CONTRACT_SHA256 = "d2eea9d27ea066884fa0986c89b3e4932c9293484569dbe45a99005b606f94fe"
CURRENT_CONTRACT_SHA256 = "f41c1497c599855322cd136a62793bccd6a549f1b372533bdc341ca18f76c1cf"
CONTRACT_ID = "s3-2-internal-paper-contract/v1"
ORDER_DETAIL_SCHEMA = "S32OrderDetail"
RECONCILE_PATH = "/api/v1/brokerage/orders/{orderId}/reconcile"
RECONCILE_409 = {
    "content": {
        "application/json": {
            "schema": {
                "$ref": "#/components/schemas/ApiResponseOrderFillReconciliationProjection"
            }
        }
    },
    "description": "Order state conflicts or the order has no KIS reconciliation evidence.",
}


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractValidationError(f"{label} must be an object.")
    return value


def _count_value(node: object, expected: str) -> int:
    if isinstance(node, dict):
        return sum(_count_value(value, expected) for value in node.values())
    if isinstance(node, list):
        return sum(_count_value(value, expected) for value in node)
    return int(node == expected)


def _status_enum(schema: dict[str, Any], label: str) -> list[str]:
    value = schema.get("enum")
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ContractValidationError(f"{label} must be a string enum.")
    return value


def project_pre_s3_2_local_order_retirement(
    current: Mapping[str, Any],
) -> dict[str, Any]:
    """Remove only V219's local order state and 409 description from the root OpenAPI."""

    projected = copy.deepcopy(_object(dict(current), "OpenAPI document"))
    if projected.get("x-s3-2-contract-id") != CONTRACT_ID:
        raise ContractValidationError("S3.2 OpenAPI contract identity drifted.")
    contract_hash = projected.get("x-s3-2-contract-sha256")
    schemas = _object(
        _object(projected.get("components"), "OpenAPI components").get("schemas"),
        "OpenAPI schemas",
    )
    order_detail = _object(schemas.get(ORDER_DETAIL_SCHEMA), ORDER_DETAIL_SCHEMA)
    paths = _object(projected.get("paths"), "OpenAPI paths")
    operation = _object(
        _object(paths.get(RECONCILE_PATH), "reconcile path").get("post"),
        "reconcile POST",
    )
    responses = _object(operation.get("responses"), "reconcile responses")

    if contract_hash == BASE_CONTRACT_SHA256:
        if _count_value(order_detail, "LOCAL_RETIRED") != 0 or "409" in responses:
            raise ContractValidationError("unversioned S3.2 local-retirement surface drifted.")
        return projected
    if contract_hash != CURRENT_CONTRACT_SHA256:
        raise ContractValidationError("S3.2 OpenAPI contract hash is not an approved revision.")

    branches = order_detail.get("allOf")
    if not isinstance(branches, list) or len(branches) != 2:
        raise ContractValidationError("S3.2 order-detail mode branches drifted.")
    kis_status = _status_enum(
        _object(
            _object(
                _object(
                    _object(branches[0], "KIS branch").get("then"), "KIS then"
                ).get("properties"),
                "KIS properties",
            ).get("status"),
            "KIS status",
        ),
        "KIS status enum",
    )
    paper_status = _status_enum(
        _object(
            _object(
                _object(
                    _object(branches[1], "paper branch").get("then"), "paper then"
                ).get("properties"),
                "paper properties",
            ).get("status"),
            "paper status",
        ),
        "paper status enum",
    )
    root_status = _status_enum(
        _object(order_detail.get("properties"), "order-detail properties").get("status"),
        "order-detail status enum",
    )
    if (
        _count_value(order_detail, "LOCAL_RETIRED") != 2
        or kis_status.count("LOCAL_RETIRED") != 1
        or paper_status.count("LOCAL_RETIRED") != 0
        or root_status.count("LOCAL_RETIRED") != 1
        or responses.get("409") != RECONCILE_409
    ):
        raise ContractValidationError("V219 S3.2 additive OpenAPI surface drifted.")

    kis_status.remove("LOCAL_RETIRED")
    root_status.remove("LOCAL_RETIRED")
    responses.pop("409")
    projected["x-s3-2-contract-sha256"] = BASE_CONTRACT_SHA256
    return projected
