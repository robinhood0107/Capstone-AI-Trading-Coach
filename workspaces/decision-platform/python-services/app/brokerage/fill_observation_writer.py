"""Sanitized offline fixture를 append-only fill observation으로 적재한다."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg

from app.brokerage.fill_observation_models import FillObservationRecord
from app.offline_fixture_io import read_json_fixture

_ROOT_FIELDS = {"schemaVersion", "sourceVersion", "observations"}
_OBSERVATION_FIELDS = {
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
}
_ORDER_ID = re.compile(r"^ord_mock_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_REF = re.compile(r"^[0-9A-Za-z._:-]{1,128}$")
_MAX_FIXTURE_BYTES = 4 * 1024 * 1024
_MAX_OBSERVATIONS = 10_000
_MAX_TEXT = 128
_MAX_BIGINT = 9_223_372_036_854_775_807


def load_fill_observation_fixture(path: Path) -> tuple[FillObservationRecord, ...]:
    """local sanitized JSON만 읽고 network·provider·production fallback 없이 검증한다."""
    artifact_bytes, root = read_json_fixture(
        path,
        max_bytes=_MAX_FIXTURE_BYTES,
        label="fill observation",
    )
    artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()
    if not isinstance(root, dict) or set(root) != _ROOT_FIELDS:
        raise ValueError("fill observation fixture root shape is invalid")
    schema_version = root["schemaVersion"]
    if schema_version != "1":
        raise ValueError("fill observation schemaVersion is invalid")
    source_version = _bounded_text(root["sourceVersion"], "sourceVersion")
    observations = root["observations"]
    if (
        not isinstance(observations, list)
        or not 1 <= len(observations) <= _MAX_OBSERVATIONS
    ):
        raise ValueError("fill observation fixture size is invalid")

    records = tuple(
        _record(
            value,
            schema_version=schema_version,
            source_version=source_version,
            artifact_hash=artifact_hash,
        )
        for value in observations
    )
    identities = [
        (record.order_id, record.provider_exec_ref_hash) for record in records
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("fill observation fixture contains duplicate execution refs")
    return tuple(
        sorted(
            records,
            key=lambda record: (
                record.observed_at,
                record.observation_id,
            ),
        )
    )


def append_fill_observation_fixture(path: Path, *, database_dsn: str) -> int:
    """decision_fill_writer DSN에서만 exact INSERT하며 충돌을 성공으로 숨기지 않는다."""
    if not database_dsn.strip():
        raise ValueError("decision fill writer database DSN is required")
    records = load_fill_observation_fixture(path)
    with psycopg.connect(database_dsn, autocommit=False) as connection:
        with connection.transaction():
            role = connection.execute("SELECT current_user").fetchone()
            if role is None or role[0] != "decision_fill_writer":
                raise PermissionError("fill observation writer role is invalid")
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO order_fill_observations (
                      observation_id, order_id, provider_exec_ref_hash, exec_type,
                      fill_quantity, fill_price_krw, cumulative_quantity,
                      leaves_quantity, average_fill_price_krw, observed_at,
                      received_at, schema_version, source_version, source_ref,
                      completeness, artifact_hash
                    ) VALUES (
                      %s, %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    [
                        (
                            record.observation_id,
                            record.order_id,
                            record.provider_exec_ref_hash,
                            record.exec_type,
                            record.fill_quantity,
                            record.fill_price_krw,
                            record.cumulative_quantity,
                            record.leaves_quantity,
                            record.average_fill_price_krw,
                            record.observed_at,
                            record.received_at,
                            record.schema_version,
                            record.source_version,
                            record.source_ref,
                            record.completeness,
                            record.artifact_hash,
                        )
                        for record in records
                    ],
                )
                return cursor.rowcount


def _record(
    value: object,
    *,
    schema_version: str,
    source_version: str,
    artifact_hash: str,
) -> FillObservationRecord:
    if not isinstance(value, dict) or set(value) != _OBSERVATION_FIELDS:
        raise ValueError("fill observation item shape is invalid")
    order_id = value["orderId"]
    provider_ref = value["providerExecRefHash"]
    if not isinstance(order_id, str) or _ORDER_ID.fullmatch(order_id) is None:
        raise ValueError("fill observation orderId is invalid")
    if (
        not isinstance(provider_ref, str)
        or _SHA256.fullmatch(provider_ref) is None
    ):
        raise ValueError("fill observation providerExecRefHash is invalid")
    exec_type = value["execType"]
    if exec_type not in {"PARTIAL_FILL", "FILL", "CANCELLED", "REJECTED"}:
        raise ValueError("fill observation execType is invalid")
    fill_quantity = _non_negative_int(value["fillQuantity"], "fillQuantity")
    fill_price = _optional_positive_int(value["fillPriceKrw"], "fillPriceKrw")
    cumulative = _non_negative_int(
        value["cumulativeQuantity"],
        "cumulativeQuantity",
    )
    leaves = _non_negative_int(value["leavesQuantity"], "leavesQuantity")
    average = _optional_positive_int(
        value["averageFillPriceKrw"],
        "averageFillPriceKrw",
    )
    if (exec_type in {"PARTIAL_FILL", "FILL"}) != (fill_price is not None):
        raise ValueError("fill observation fill price pairing is invalid")
    if exec_type in {"PARTIAL_FILL", "FILL"} and fill_quantity == 0:
        raise ValueError("fill observation fillQuantity is invalid")
    if exec_type in {"CANCELLED", "REJECTED"} and fill_quantity != 0:
        raise ValueError("fill observation terminal fillQuantity is invalid")
    if fill_quantity > cumulative:
        raise ValueError("fill observation cumulativeQuantity is invalid")

    observed_at = _aware_datetime(value["observedAt"], "observedAt")
    received_at = _aware_datetime(value["receivedAt"], "receivedAt")
    if received_at < observed_at:
        raise ValueError("fill observation receivedAt precedes observedAt")
    completeness = value["completeness"]
    if completeness not in {"COMPLETE", "PARTIAL"}:
        raise ValueError("fill observation completeness is invalid")
    source_ref = value["sourceRef"]
    if (
        not isinstance(source_ref, str)
        or _SOURCE_REF.fullmatch(source_ref) is None
    ):
        raise ValueError("fill observation sourceRef is invalid")

    identity = _canonical_json(
        {
            "artifactHash": artifact_hash,
            "cumulativeQuantity": cumulative,
            "execType": exec_type,
            "observedAt": observed_at.isoformat(),
            "orderId": order_id,
            "providerExecRefHash": provider_ref,
            "schemaVersion": schema_version,
            "sourceVersion": source_version,
        }
    )
    observation_id = f"ofo_{hashlib.sha256(identity.encode()).hexdigest()[:32]}"
    return FillObservationRecord(
        observation_id=observation_id,
        order_id=order_id,
        provider_exec_ref_hash=provider_ref,
        exec_type=exec_type,
        fill_quantity=fill_quantity,
        fill_price_krw=fill_price,
        cumulative_quantity=cumulative,
        leaves_quantity=leaves,
        average_fill_price_krw=average,
        observed_at=observed_at,
        received_at=received_at,
        schema_version=schema_version,
        source_version=source_version,
        source_ref=source_ref,
        completeness=completeness,
        artifact_hash=artifact_hash,
    )


def _bounded_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= _MAX_TEXT:
        raise ValueError(f"fill observation {field} is invalid")
    return value


def _aware_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"fill observation {field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"fill observation {field} is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError(f"fill observation {field} must include an offset")
    return parsed


def _non_negative_int(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_BIGINT:
        raise ValueError(f"fill observation {field} is invalid")
    return value


def _optional_positive_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    parsed = _non_negative_int(value, field)
    if parsed == 0:
        raise ValueError(f"fill observation {field} is invalid")
    return parsed


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
