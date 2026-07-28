from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from contracts.generate_principle_contracts import (  # noqa: E402
    ContractValidationError,
    load_json_bytes_strict,
)


CATALOG_PATH = REPO_ROOT / "contracts/catalogs/s3-2-internal-paper-contract.v1.json"
LONG_MAX = 9_223_372_036_854_775_807


def _object(
    properties: dict[str, object],
    required: list[str],
    *,
    title: str | None = None,
) -> dict[str, object]:
    schema: dict[str, object] = {
        "additionalProperties": False,
        "properties": properties,
        "required": required,
        "type": "object",
    }
    if title is not None:
        schema["title"] = title
    return schema


def _integer(minimum: int = 0, maximum: int = LONG_MAX) -> dict[str, object]:
    return {"maximum": maximum, "minimum": minimum, "type": "integer"}


def _document(schema_id: str, schema: dict[str, object]) -> dict[str, object]:
    return {
        "$id": f"contracts/schemas/{schema_id}.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **schema,
    }


def _order_intent_schema() -> dict[str, object]:
    return _object(
        {
            "estimatedAmount": _integer(1),
            "estimatedPrice": _integer(1),
            "orderType": {"enum": ["MARKET", "LIMIT"]},
            "quantity": _integer(1),
            "side": {"enum": ["BUY", "SELL"]},
            "strategyId": {"maxLength": 120, "minLength": 1, "type": "string"},
            "symbol": {
                "maxLength": 20,
                "minLength": 1,
                "pattern": "^[0-9A-Z._:-]{1,20}$",
                "type": "string",
            },
            "timeframe": {"enum": ["1d", "60m"]},
        },
        [
            "symbol",
            "side",
            "orderType",
            "quantity",
            "estimatedPrice",
            "estimatedAmount",
            "timeframe",
            "strategyId",
        ],
    )


def _request_schema() -> dict[str, object]:
    return _document(
        "s3-2-paper-order-request",
        _object(
            {
                "decisionId": {"pattern": "^dec_[0-9a-f]{32}$", "type": "string"},
                "orderIntent": _order_intent_schema(),
                "userAcknowledgement": _object(
                    {"warningsAccepted": {"type": "boolean"}},
                    ["warningsAccepted"],
                ),
            },
            ["decisionId", "orderIntent", "userAcknowledgement"],
            title="S3.2 INTERNAL_PAPER order submit request v1",
        ),
    )


def _fill_schema(catalog: dict[str, Any]) -> dict[str, object]:
    return _object(
        {
            "amountKrw": _integer(1),
            "feeModel": {"const": catalog["feeModel"]},
            "observedAt": {"format": "date-time", "type": "string"},
            "priceBasis": {"enum": catalog["priceBasis"]},
            "priceKrw": _integer(1),
            "quantity": _integer(1),
            "slippageBps": _integer(0, catalog["slippageBpsMaximum"]),
        },
        [
            "quantity",
            "priceKrw",
            "amountKrw",
            "priceBasis",
            "slippageBps",
            "feeModel",
            "observedAt",
        ],
    )


def _response_schema(catalog: dict[str, Any]) -> dict[str, object]:
    base = _object(
        {
            "accountId": {"pattern": catalog["accountIdPattern"], "type": "string"},
            "brokerageMode": {"const": catalog["brokerageMode"]},
            "fill": {"oneOf": [_fill_schema(catalog), {"type": "null"}]},
            "orderId": {"pattern": catalog["orderIdPattern"], "type": "string"},
            "status": {"enum": ["ACCEPTED", "FILLED"]},
            "submittedAt": {"format": "date-time", "type": "string"},
        },
        ["orderId", "accountId", "brokerageMode", "status", "submittedAt", "fill"],
        title="S3.2 INTERNAL_PAPER order response v1",
    )
    base["allOf"] = [
        {
            "if": {
                "properties": {"status": {"const": "FILLED"}},
                "required": ["status"],
            },
            "then": {"properties": {"fill": _fill_schema(catalog)}},
        },
        {
            "if": {
                "properties": {"status": {"const": "ACCEPTED"}},
                "required": ["status"],
            },
            "then": {"properties": {"fill": {"type": "null"}}},
        },
    ]
    return _document("s3-2-paper-order-response", base)


def _order_detail_schema(catalog: dict[str, Any]) -> dict[str, object]:
    base = _object(
        {
            "accountId": {"pattern": catalog["accountIdPattern"], "type": "string"},
            "brokerageMode": {"enum": ["KIS_MOCK", catalog["brokerageMode"]]},
            "decisionId": {"pattern": "^dec_[0-9a-f]{32}$", "type": "string"},
            "orderId": {
                "oneOf": [
                    {"pattern": "^ord_mock_[0-9a-f]{32}$", "type": "string"},
                    {"pattern": catalog["orderIdPattern"], "type": "string"},
                ]
            },
            "status": {"enum": catalog["sharedOrderStatuses"]},
            "submittedAt": {"format": "date-time", "type": "string"},
        },
        [
            "orderId",
            "accountId",
            "brokerageMode",
            "status",
            "submittedAt",
            "decisionId",
        ],
        title="S3.2 shared brokerage order detail response v1",
    )
    base["allOf"] = [
        {
            "if": {
                "properties": {"brokerageMode": {"const": "KIS_MOCK"}},
                "required": ["brokerageMode"],
            },
            "then": {
                "properties": {
                    "orderId": {
                        "pattern": "^ord_mock_[0-9a-f]{32}$",
                        "type": "string",
                    },
                    "status": {"enum": catalog["sharedOrderStatuses"]},
                }
            },
        },
        {
            "if": {
                "properties": {"brokerageMode": {"const": catalog["brokerageMode"]}},
                "required": ["brokerageMode"],
            },
            "then": {
                "properties": {
                    "orderId": {
                        "pattern": catalog["orderIdPattern"],
                        "type": "string",
                    },
                    "status": {"enum": catalog["statuses"]},
                }
            },
        },
    ]
    return _document("s3-2-order-detail", base)


def _balance_schema(catalog: dict[str, Any]) -> dict[str, object]:
    position = _object(
        {
            "averagePriceKrw": _integer(),
            "marketValueKrw": _integer(),
            "quantity": _integer(),
            "symbol": {"pattern": "^[0-9A-Z._:-]{1,20}$", "type": "string"},
        },
        ["symbol", "quantity", "marketValueKrw", "averagePriceKrw"],
    )
    return _document(
        "s3-2-paper-balance",
        _object(
            {
                "accountId": {"pattern": catalog["accountIdPattern"], "type": "string"},
                "asOf": {"format": "date-time", "type": "string"},
                "brokerageMode": {"const": catalog["brokerageMode"]},
                "cashKrw": _integer(),
                "positions": {"items": position, "maxItems": 1000, "type": "array"},
                "totalEquityKrw": _integer(),
                "valuationBasis": {"const": catalog["valuationBasis"]},
            },
            [
                "accountId",
                "brokerageMode",
                "cashKrw",
                "totalEquityKrw",
                "positions",
                "asOf",
                "valuationBasis",
            ],
            title="S3.2 INTERNAL_PAPER balance response v1",
        ),
    )


def _buyable_schema(catalog: dict[str, Any]) -> dict[str, object]:
    return _document(
        "s3-2-paper-buyable",
        _object(
            {
                "accountId": {"pattern": catalog["accountIdPattern"], "type": "string"},
                "asOf": {"format": "date-time", "type": "string"},
                "brokerageMode": {"const": catalog["brokerageMode"]},
                "buyableAmountKrw": _integer(),
                "buyableQuantity": _integer(),
                "cashKrw": _integer(),
                "estimatedPrice": _integer(1),
                "symbol": {"pattern": "^[0-9A-Z._:-]{1,20}$", "type": "string"},
            },
            [
                "accountId",
                "brokerageMode",
                "symbol",
                "estimatedPrice",
                "buyableQuantity",
                "buyableAmountKrw",
                "cashKrw",
                "asOf",
            ],
            title="S3.2 INTERNAL_PAPER buyable response v1",
        ),
    )


def _examples(catalog: dict[str, Any]) -> dict[str, object]:
    request = {
        "decisionId": "dec_11111111111111111111111111111111",
        "orderIntent": {
            "estimatedAmount": 720000,
            "estimatedPrice": 72000,
            "orderType": "MARKET",
            "quantity": 10,
            "side": "BUY",
            "strategyId": "paper-demo-v1",
            "symbol": "005930",
            "timeframe": "1d",
        },
        "userAcknowledgement": {"warningsAccepted": True},
    }
    return {
        "contracts/examples/s3-2-paper-order-request.valid.json": request,
        "contracts/examples/invalid/s3-2-paper-order-request.account.invalid.json": {
            **request,
            "accountId": "acct_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        },
        "contracts/examples/invalid/s3-2-paper-order-request.mode.invalid.json": {
            **request,
            "brokerageMode": "INTERNAL_PAPER",
        },
        "contracts/examples/invalid/s3-2-paper-order-request.price.invalid.json": {
            **request,
            "orderIntent": {**request["orderIntent"], "estimatedPrice": 0},
        },
        "contracts/examples/invalid/s3-2-paper-order-request.symbol.invalid.json": {
            **request,
            "orderIntent": {**request["orderIntent"], "symbol": "005930;DROP"},
        },
        "contracts/examples/s3-2-paper-order-response.filled.valid.json": {
            "accountId": "acct_cccccccccccccccccccccccccccccccc",
            "brokerageMode": catalog["brokerageMode"],
            "fill": {
                "amountKrw": 720360,
                "feeModel": catalog["feeModel"],
                "observedAt": "2026-07-27T01:09:58Z",
                "priceBasis": "LAST_QUOTE",
                "priceKrw": 72036,
                "quantity": 10,
                "slippageBps": catalog["slippageBpsDefault"],
            },
            "orderId": "ord_paper_0123456789abcdef0123456789abcdef",
            "status": "FILLED",
            "submittedAt": "2026-07-27T01:10:01Z",
        },
        "contracts/examples/s3-2-paper-order-response.accepted.valid.json": {
            "accountId": "acct_cccccccccccccccccccccccccccccccc",
            "brokerageMode": catalog["brokerageMode"],
            "fill": None,
            "orderId": "ord_paper_fedcba9876543210fedcba9876543210",
            "status": "ACCEPTED",
            "submittedAt": "2026-07-27T01:10:01Z",
        },
        "contracts/examples/s3-2-order-detail.valid.json": {
            "accountId": "acct_cccccccccccccccccccccccccccccccc",
            "brokerageMode": catalog["brokerageMode"],
            "decisionId": "dec_11111111111111111111111111111111",
            "orderId": "ord_paper_0123456789abcdef0123456789abcdef",
            "status": "FILLED",
            "submittedAt": "2026-07-27T01:10:01Z",
        },
        "contracts/examples/s3-2-paper-balance.valid.json": {
            "accountId": "acct_cccccccccccccccccccccccccccccccc",
            "asOf": "2026-07-27T01:10:01Z",
            "brokerageMode": catalog["brokerageMode"],
            "cashKrw": 9279640,
            "positions": [
                {
                    "averagePriceKrw": 72036,
                    "marketValueKrw": 720360,
                    "quantity": 10,
                    "symbol": "005930",
                }
            ],
            "totalEquityKrw": 10000000,
            "valuationBasis": catalog["valuationBasis"],
        },
        "contracts/examples/s3-2-paper-buyable.valid.json": {
            "accountId": "acct_cccccccccccccccccccccccccccccccc",
            "asOf": "2026-07-27T01:10:01Z",
            "brokerageMode": catalog["brokerageMode"],
            "buyableAmountKrw": 9216000,
            "buyableQuantity": 128,
            "cashKrw": 9279640,
            "estimatedPrice": 72000,
            "symbol": "005930",
        },
    }


def _validate_catalog(catalog: object) -> dict[str, Any]:
    if not isinstance(catalog, dict):
        raise ContractValidationError("S3.2 contract catalog must be an object.")
    required = {
        "accountIdPattern",
        "brokerageMode",
        "contractId",
        "evidence",
        "feeModel",
        "orderIdPattern",
        "orderIntentFields",
        "priceBasis",
        "requestFields",
        "routes",
        "sharedOrderStatuses",
        "slippageBpsDefault",
        "slippageBpsMaximum",
        "statuses",
        "valuationBasis",
        "warningCodes",
    }
    if set(catalog) != required:
        raise ContractValidationError("S3.2 contract catalog fields drifted.")
    if catalog["contractId"] != "s3-2-internal-paper-contract/v1":
        raise ContractValidationError("S3.2 contract id drifted.")
    if catalog["brokerageMode"] != "INTERNAL_PAPER":
        raise ContractValidationError("S3.2 brokerage mode drifted.")
    if set(catalog["requestFields"]) != {
        "decisionId",
        "orderIntent",
        "userAcknowledgement",
    }:
        raise ContractValidationError("S3.2 request fields drifted.")
    if set(catalog["orderIntentFields"]) != {
        "symbol",
        "side",
        "orderType",
        "quantity",
        "estimatedPrice",
        "estimatedAmount",
        "timeframe",
        "strategyId",
    }:
        raise ContractValidationError("S3.2 order intent fields drifted.")
    if catalog["priceBasis"] != ["LAST_QUOTE", "PREVIOUS_CLOSE"]:
        raise ContractValidationError("S3.2 price basis drifted.")
    if catalog["slippageBpsDefault"] != 5 or catalog["slippageBpsMaximum"] != 100:
        raise ContractValidationError("S3.2 slippage bounds drifted.")
    if set(catalog["sharedOrderStatuses"]) != {
        "SUBMITTED",
        "PENDING_RECONCILIATION",
        "ACCEPTED",
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCEL_REQUESTED",
        "CANCELLED",
        "REJECTED",
    }:
        raise ContractValidationError("S3.2 shared order statuses drifted.")
    evidence = catalog["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != {
        "auditActions",
        "eventTypes",
        "filledExactKeys",
    }:
        raise ContractValidationError("S3.2 evidence catalog drifted.")
    return catalog


def load_catalog() -> dict[str, Any]:
    return _validate_catalog(
        load_json_bytes_strict(
            CATALOG_PATH.read_bytes(),
            source="contracts/catalogs/s3-2-internal-paper-contract.v1.json",
        )
    )


def generated_artifacts(catalog: dict[str, Any]) -> dict[str, object]:
    artifacts: dict[str, object] = {
        "contracts/schemas/s3-2-order-detail.schema.json": _order_detail_schema(
            catalog
        ),
        "contracts/schemas/s3-2-paper-balance.schema.json": _balance_schema(catalog),
        "contracts/schemas/s3-2-paper-buyable.schema.json": _buyable_schema(catalog),
        "contracts/schemas/s3-2-paper-order-request.schema.json": _request_schema(),
        "contracts/schemas/s3-2-paper-order-response.schema.json": _response_schema(
            catalog
        ),
    }
    artifacts.update(_examples(catalog))
    return artifacts


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()


def _write_atomic(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise ContractValidationError(
            f"Refusing to replace symlink: {path.relative_to(REPO_ROOT)}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def generate(*, check: bool) -> int:
    catalog = load_catalog()
    failures: list[str] = []
    for relative_path, value in generated_artifacts(catalog).items():
        path = REPO_ROOT / relative_path
        expected = canonical_json_bytes(value)
        if check:
            if not path.is_file() or path.read_bytes() != expected:
                failures.append(relative_path)
            continue
        _write_atomic(path, expected)
    if failures:
        for failure in failures:
            print(f"DRIFT {failure}")
        return 1
    print("PASS S3.2 generated contract artifacts are canonical")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    return generate(check=arguments.check)


if __name__ == "__main__":
    raise SystemExit(main())
