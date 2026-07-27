from __future__ import annotations

import argparse
import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPO_ROOT / "contracts/catalogs/s3-3-fill-contract.v1.json"
LONG_MAX = 9_223_372_036_854_775_807


class ContractValidationError(ValueError):
    """S3.3 catalog/schema/fixture가 잠긴 v1 계약을 위반할 때 발생한다."""


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractValidationError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_constant(token: str) -> None:
    raise ContractValidationError(f"Non-finite JSON number is forbidden: {token}")


def load_json_bytes_strict(raw: bytes, *, source: str) -> Any:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ContractValidationError(f"{source}: UTF-8 BOM is forbidden.")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ContractValidationError(f"{source}: invalid UTF-8.") from error
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ContractValidationError) as error:
        if isinstance(error, ContractValidationError):
            raise
        raise ContractValidationError(
            f"{source}: invalid JSON: {error.msg}"
        ) from error


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


def _nullable(schema: dict[str, object]) -> dict[str, object]:
    return {"oneOf": [schema, {"type": "null"}]}


def _document(schema_id: str, schema: dict[str, object]) -> dict[str, object]:
    return {
        "$id": f"contracts/schemas/{schema_id}.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **schema,
    }


def _order_id(catalog: dict[str, Any]) -> dict[str, object]:
    return {
        "oneOf": [
            {
                "pattern": catalog["orderIdPatterns"]["KIS_MOCK"],
                "type": "string",
            },
            {
                "pattern": catalog["orderIdPatterns"]["INTERNAL_PAPER"],
                "type": "string",
            },
        ]
    }


def _mode_order_pair_constraints(catalog: dict[str, Any]) -> list[dict[str, object]]:
    return [
        {
            "if": {
                "properties": {"brokerageMode": {"const": mode}},
                "required": ["brokerageMode"],
            },
            "then": {
                "properties": {
                    "orderId": {
                        "pattern": catalog["orderIdPatterns"][mode],
                        "type": "string",
                    }
                }
            },
        }
        for mode in catalog["brokerageModes"]
    ]


def _fill_observation_item(catalog: dict[str, Any]) -> dict[str, object]:
    item = _object(
        {
            "averageFillPriceKrw": _nullable(_integer(1)),
            "completeness": {"enum": catalog["fillObservation"]["completeness"]},
            "cumulativeQuantity": _integer(),
            "execType": {"enum": catalog["fillObservation"]["execTypes"]},
            "fillPriceKrw": _nullable(_integer(1)),
            "fillQuantity": _integer(),
            "leavesQuantity": _integer(),
            "observedAt": {"format": "date-time", "type": "string"},
            "orderId": {
                "pattern": catalog["orderIdPatterns"]["KIS_MOCK"],
                "type": "string",
            },
            "providerExecRefHash": {
                "pattern": "^[0-9a-f]{64}$",
                "type": "string",
            },
            "receivedAt": {"format": "date-time", "type": "string"},
            "sourceRef": {
                "maxLength": catalog["fillObservation"]["sourceTextMaximum"],
                "minLength": 1,
                "pattern": "^[0-9A-Za-z._:-]+$",
                "type": "string",
            },
        },
        [
            "orderId",
            "providerExecRefHash",
            "execType",
            "fillQuantity",
            "fillPriceKrw",
            "cumulativeQuantity",
            "leavesQuantity",
            "averageFillPriceKrw",
            "observedAt",
            "receivedAt",
            "completeness",
            "sourceRef",
        ],
    )
    item["allOf"] = [
        {
            "if": {
                "properties": {
                    "execType": {"enum": ["PARTIAL_FILL", "FILL"]}
                },
                "required": ["execType"],
            },
            "then": {
                "properties": {
                    "fillPriceKrw": _integer(1),
                    "fillQuantity": _integer(1),
                }
            },
            "else": {
                "properties": {
                    "fillPriceKrw": {"type": "null"},
                    "fillQuantity": {"const": 0},
                }
            },
        }
    ]
    return item


def _fill_observation_schema(catalog: dict[str, Any]) -> dict[str, object]:
    return _document(
        "s3-3-fill-observation",
        _object(
            {
                "observations": {
                    "items": _fill_observation_item(catalog),
                    "maxItems": catalog["fillObservation"]["fixtureItemsMaximum"],
                    "minItems": 1,
                    "type": "array",
                },
                "schemaVersion": {
                    "const": catalog["fillObservation"]["schemaVersion"]
                },
                "sourceVersion": {
                    "maxLength": catalog["fillObservation"]["sourceTextMaximum"],
                    "minLength": 1,
                    "type": "string",
                },
            },
            ["schemaVersion", "sourceVersion", "observations"],
            title="S3.3 sanitized offline fill observation fixture v1",
        ),
    )


def _reconcile_schema(catalog: dict[str, Any]) -> dict[str, object]:
    reconciliation = _object(
        {
            "checkedAt": _nullable({"format": "date-time", "type": "string"}),
            "status": {"enum": catalog["reconciliationStatuses"]},
        },
        ["status", "checkedAt"],
    )
    reconciliation["allOf"] = [
        {
            "if": {
                "properties": {"status": {"const": "NOT_APPLICABLE"}},
                "required": ["status"],
            },
            "then": {"properties": {"checkedAt": {"type": "null"}}},
            "else": {
                "properties": {
                    "checkedAt": {"format": "date-time", "type": "string"}
                }
            },
        }
    ]
    schema = _object(
        {
            "appliedEventCount": _integer(
                0,
                catalog["reconcileObservationMaximum"],
            ),
            "averageFillPriceKrw": _nullable(_integer(1)),
            "brokerageMode": {"enum": catalog["brokerageModes"]},
            "filledQuantity": _integer(),
            "hasMore": {"type": "boolean"},
            "leavesQuantity": _integer(),
            "orderId": _order_id(catalog),
            "reconciliation": reconciliation,
            "status": {"enum": catalog["orderStatuses"]},
            "unfilledTerminatedQuantity": _integer(),
        },
        [
            "orderId",
            "brokerageMode",
            "status",
            "filledQuantity",
            "leavesQuantity",
            "unfilledTerminatedQuantity",
            "averageFillPriceKrw",
            "reconciliation",
            "appliedEventCount",
            "hasMore",
        ],
        title="S3.3 sanitized order reconciliation response v1",
    )
    schema["allOf"] = _mode_order_pair_constraints(catalog)
    return _document("s3-3-reconcile-response", schema)


def _fill_item(catalog: dict[str, Any]) -> dict[str, object]:
    item = _object(
        {
            "brokerageMode": {"enum": catalog["brokerageModes"]},
            "execRefHash": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
            "fillAmountKrw": _integer(1),
            "fillPriceKrw": _integer(1),
            "fillQuantity": _integer(1),
            "filledAt": {"format": "date-time", "type": "string"},
            "orderId": _order_id(catalog),
            "side": {"enum": ["BUY", "SELL"]},
            "symbol": {
                "maxLength": 20,
                "minLength": 1,
                "pattern": "^[0-9A-Z._:-]{1,20}$",
                "type": "string",
            },
        },
        [
            "orderId",
            "brokerageMode",
            "symbol",
            "side",
            "fillQuantity",
            "fillPriceKrw",
            "fillAmountKrw",
            "filledAt",
            "execRefHash",
        ],
    )
    item["allOf"] = _mode_order_pair_constraints(catalog)
    return item


def _fill_page_schema(catalog: dict[str, Any]) -> dict[str, object]:
    return _document(
        "s3-3-fill-page",
        _object(
            {
                "items": {
                    "items": _fill_item(catalog),
                    "maxItems": catalog["cursor"]["pageSizeMaximum"],
                    "type": "array",
                },
                "nextCursor": _nullable(
                    {
                        "maxLength": catalog["cursor"]["maximumLength"],
                        "minLength": 1,
                        "pattern": "^[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+$",
                        "type": "string",
                    }
                ),
            },
            ["items", "nextCursor"],
            title="S3.3 owner-scoped fill page v1",
        ),
    )


def _examples(catalog: dict[str, Any]) -> dict[str, object]:
    observation = {
        "averageFillPriceKrw": 70000,
        "completeness": "COMPLETE",
        "cumulativeQuantity": 1,
        "execType": "PARTIAL_FILL",
        "fillPriceKrw": 70000,
        "fillQuantity": 1,
        "leavesQuantity": 1,
        "observedAt": "2030-01-02T00:00:10Z",
        "orderId": "ord_mock_0123456789abcdef0123456789abcdef",
        "providerExecRefHash": "a" * 64,
        "receivedAt": "2030-01-02T00:00:11Z",
        "sourceRef": "offline-fill-fixture-001",
    }
    fixture = {
        "observations": [observation],
        "schemaVersion": catalog["fillObservation"]["schemaVersion"],
        "sourceVersion": "s3.3-fill-observation-v1",
    }
    reconcile = {
        "appliedEventCount": 2,
        "averageFillPriceKrw": 70000,
        "brokerageMode": "KIS_MOCK",
        "filledQuantity": 2,
        "hasMore": False,
        "leavesQuantity": 0,
        "orderId": "ord_mock_0123456789abcdef0123456789abcdef",
        "reconciliation": {
            "checkedAt": "2030-01-02T00:00:20Z",
            "status": "MATCHED",
        },
        "status": "FILLED",
        "unfilledTerminatedQuantity": 0,
    }
    fill_page = {
        "items": [
            {
                "brokerageMode": "KIS_MOCK",
                "execRefHash": "a" * 64,
                "fillAmountKrw": 70000,
                "fillPriceKrw": 70000,
                "fillQuantity": 1,
                "filledAt": "2030-01-02T00:00:10Z",
                "orderId": "ord_mock_0123456789abcdef0123456789abcdef",
                "side": "BUY",
                "symbol": "005930",
            }
        ],
        "nextCursor": None,
    }
    return {
        "contracts/examples/s3-3-fill-observation.valid.json": fixture,
        "contracts/examples/invalid/s3-3-fill-observation.raw-ref.invalid.json": {
            **fixture,
            "observations": [
                {
                    **observation,
                    "providerExecRef": "provider-exec-raw",
                }
            ],
        },
        "contracts/examples/invalid/"
        "s3-3-fill-observation.terminal-quantity.invalid.json": {
            **fixture,
            "observations": [
                {
                    **observation,
                    "averageFillPriceKrw": None,
                    "execType": "CANCELLED",
                    "fillPriceKrw": None,
                    "fillQuantity": 1,
                }
            ],
        },
        "contracts/examples/invalid/s3-3-fill-observation.unknown.invalid.json": {
            **fixture,
            "providerPayload": {"raw": True},
        },
        "contracts/examples/s3-3-reconcile-response.valid.json": reconcile,
        "contracts/examples/invalid/"
        "s3-3-reconcile-response.account.invalid.json": {
            **reconcile,
            "accountId": "acct_cccccccccccccccccccccccccccccccc",
        },
        "contracts/examples/s3-3-fill-page.valid.json": fill_page,
        "contracts/examples/invalid/s3-3-fill-page.raw-ref.invalid.json": {
            **fill_page,
            "items": [
                {
                    **fill_page["items"][0],
                    "providerExecRef": "provider-exec-raw",
                }
            ],
        },
    }


def _validate_catalog(catalog: object) -> dict[str, Any]:
    if not isinstance(catalog, dict):
        raise ContractValidationError("S3.3 fill contract catalog must be an object.")
    required = {
        "accountIdPattern",
        "brokerageModes",
        "contractId",
        "cursor",
        "fillObservation",
        "orderIdPatterns",
        "orderStatuses",
        "reconcileObservationMaximum",
        "reconciliationStatuses",
        "routes",
        "warningCodes",
    }
    if set(catalog) != required:
        raise ContractValidationError("S3.3 fill contract catalog fields drifted.")
    if catalog["contractId"] != "s3-3-fill-contract/v1":
        raise ContractValidationError("S3.3 fill contract id drifted.")
    if set(catalog["brokerageModes"]) != {"KIS_MOCK", "INTERNAL_PAPER"}:
        raise ContractValidationError("S3.3 brokerage modes drifted.")
    if set(catalog["routes"]) != {"mockFills", "paperFills", "reconcile"}:
        raise ContractValidationError("S3.3 fill route names drifted.")
    if set(catalog["routes"].values()) != {
        "GET /api/v1/brokerage/mock/accounts/{accountId}/fills",
        "GET /api/v1/brokerage/paper/accounts/{accountId}/fills",
        "POST /api/v1/brokerage/orders/{orderId}/reconcile",
    }:
        raise ContractValidationError("S3.3 fill routes drifted.")
    if catalog["reconcileObservationMaximum"] != 200:
        raise ContractValidationError("S3.3 reconciliation bound drifted.")
    cursor = catalog["cursor"]
    if not isinstance(cursor, dict) or set(cursor) != {
        "dateRangeDaysMaximum",
        "maximumLength",
        "pageSizeMaximum",
        "sort",
        "ttlSeconds",
    }:
        raise ContractValidationError("S3.3 cursor contract drifted.")
    if (
        cursor["dateRangeDaysMaximum"] != 31
        or cursor["maximumLength"] != 1024
        or cursor["pageSizeMaximum"] != 50
        or cursor["ttlSeconds"] != 900
        or cursor["sort"]
        != ["filledAt DESC", "orderId DESC", "execRefHash DESC"]
    ):
        raise ContractValidationError("S3.3 cursor bounds drifted.")
    observation = catalog["fillObservation"]
    if not isinstance(observation, dict) or set(observation) != {
        "completeness",
        "execTypes",
        "fixtureBytesMaximum",
        "fixtureItemsMaximum",
        "schemaVersion",
        "sourceTextMaximum",
    }:
        raise ContractValidationError("S3.3 fill observation contract drifted.")
    if (
        set(observation["execTypes"])
        != {"PARTIAL_FILL", "FILL", "CANCELLED", "REJECTED"}
        or set(observation["completeness"]) != {"COMPLETE", "PARTIAL"}
        or observation["fixtureBytesMaximum"] != 4 * 1024 * 1024
        or observation["fixtureItemsMaximum"] != 10_000
        or observation["schemaVersion"] != "1"
        or observation["sourceTextMaximum"] != 128
    ):
        raise ContractValidationError("S3.3 fill observation bounds drifted.")
    if set(catalog["reconciliationStatuses"]) != {
        "NOT_APPLICABLE",
        "MATCHED",
        "MISMATCH",
    }:
        raise ContractValidationError("S3.3 reconciliation statuses drifted.")
    return catalog


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()


def load_catalog() -> dict[str, Any]:
    raw = CATALOG_PATH.read_bytes()
    catalog = _validate_catalog(
        load_json_bytes_strict(
            raw,
            source="contracts/catalogs/s3-3-fill-contract.v1.json",
        )
    )
    if raw != canonical_json_bytes(catalog):
        raise ContractValidationError("S3.3 fill catalog bytes are not canonical.")
    return catalog


def generated_artifacts(catalog: dict[str, Any]) -> dict[str, object]:
    artifacts: dict[str, object] = {
        "contracts/schemas/s3-3-fill-observation.schema.json": (
            _fill_observation_schema(catalog)
        ),
        "contracts/schemas/s3-3-fill-page.schema.json": _fill_page_schema(catalog),
        "contracts/schemas/s3-3-reconcile-response.schema.json": (
            _reconcile_schema(catalog)
        ),
    }
    artifacts.update(_examples(catalog))
    return artifacts


def _write_atomic(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise ContractValidationError(
            f"Refusing to replace symlink: {path.relative_to(REPO_ROOT)}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
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
    print("PASS S3.3 generated fill contract artifacts are canonical")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    return generate(check=arguments.check)


if __name__ == "__main__":
    raise SystemExit(main())
