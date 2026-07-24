"""S1.6 sanitized symbol-corp_code registry fixture를 append-only source로 적재한다."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg

from app.offline_fixture_io import read_bounded_fixture

_ROOT_FIELDS = {
    "schemaVersion",
    "sourceVersion",
    "observedAt",
    "receivedAt",
    "mappings",
}
_MAPPING_FIELDS = {
    "symbol",
    "corpCode",
    "registryStatus",
    "completeness",
}
_SYMBOL = re.compile(r"^[0-9]{6}$")
_CORP_CODE = re.compile(r"^[0-9]{8}$")
_MAX_MAPPINGS = 50_000
_MAX_PAYLOAD_BYTES = 65_536
_MAX_FIXTURE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CorporationRegistryObservation:
    """raw provider 응답 없이 symbol-corp_code 상태와 provenance만 운반한다."""

    observation_id: str
    symbol: str
    corp_code: str
    registry_status: str
    completeness: str
    observed_at: datetime
    received_at: datetime
    schema_version: str
    source_version: str
    payload_json: str
    source_ref: str
    artifact_hash: str


def load_corporation_registry_fixture(
    path: Path,
) -> tuple[CorporationRegistryObservation, ...]:
    """versioned sanitized local JSON만 읽고 network나 기본 mapping 없이 observation을 만든다."""
    artifact_bytes = read_bounded_fixture(
        path,
        max_bytes=_MAX_FIXTURE_BYTES,
        label="corporation registry",
    )
    artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()
    root = json.loads(artifact_bytes)
    if not isinstance(root, dict) or set(root) != _ROOT_FIELDS:
        raise ValueError("corporation registry fixture root shape is invalid")
    schema_version = _bounded_text(root["schemaVersion"], "schemaVersion")
    source_version = _bounded_text(root["sourceVersion"], "sourceVersion")
    observed_at = _aware_datetime(root["observedAt"], "observedAt")
    received_at = _aware_datetime(root["receivedAt"], "receivedAt")
    if received_at < observed_at:
        raise ValueError("corporation registry receivedAt precedes observedAt")
    mappings = root["mappings"]
    if not isinstance(mappings, list) or not 1 <= len(mappings) <= _MAX_MAPPINGS:
        raise ValueError("corporation registry fixture size is invalid")

    observations = tuple(
        _observation(
            mapping,
            schema_version=schema_version,
            source_version=source_version,
            observed_at=observed_at,
            received_at=received_at,
            artifact_hash=artifact_hash,
        )
        for mapping in mappings
    )
    identities = [(row.symbol, row.corp_code) for row in observations]
    if len(identities) != len(set(identities)):
        raise ValueError("corporation registry fixture contains duplicate mappings")
    return tuple(
        sorted(
            observations,
            key=lambda row: (row.symbol, row.corp_code),
        )
    )


def append_corporation_registry_fixture(path: Path, *, database_dsn: str) -> int:
    """decision_collector DSN으로 exact INSERT만 수행하고 동일 observation은 no-op 처리한다."""
    if not database_dsn.strip():
        raise ValueError("decision collector database DSN is required")
    observations = load_corporation_registry_fixture(path)
    inserted = 0
    with psycopg.connect(database_dsn, autocommit=False) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO corporation_registry_observations (
                      observation_id, symbol, corp_code, registry_status,
                      completeness, observed_at, received_at, schema_version,
                      source_version, payload_json, source_ref, artifact_hash
                    ) VALUES (
                      %s, %s, %s, %s,
                      %s, %s, %s, %s,
                      %s, %s::jsonb, %s, %s
                    )
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        (
                            observation.observation_id,
                            observation.symbol,
                            observation.corp_code,
                            observation.registry_status,
                            observation.completeness,
                            observation.observed_at,
                            observation.received_at,
                            observation.schema_version,
                            observation.source_version,
                            observation.payload_json,
                            observation.source_ref,
                            observation.artifact_hash,
                        )
                        for observation in observations
                    ],
                )
                inserted = cursor.rowcount
    return inserted


def _observation(
    value: object,
    *,
    schema_version: str,
    source_version: str,
    observed_at: datetime,
    received_at: datetime,
    artifact_hash: str,
) -> CorporationRegistryObservation:
    if not isinstance(value, dict) or set(value) != _MAPPING_FIELDS:
        raise ValueError("corporation registry mapping shape is invalid")
    symbol = value["symbol"]
    if not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None:
        raise ValueError("corporation registry symbol is invalid")
    corp_code = value["corpCode"]
    if not isinstance(corp_code, str) or _CORP_CODE.fullmatch(corp_code) is None:
        raise ValueError("corporation registry corpCode is invalid")
    registry_status = value["registryStatus"]
    if registry_status not in {"ACTIVE", "INACTIVE"}:
        raise ValueError("corporation registry status is invalid")
    completeness = value["completeness"]
    if completeness not in {"COMPLETE", "PARTIAL"}:
        raise ValueError("corporation registry completeness is invalid")

    payload = {
        "completeness": completeness,
        "corpCode": corp_code,
        "registryStatus": registry_status,
        "symbol": symbol,
    }
    payload_json = _canonical_json(payload)
    if len(payload_json.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError("corporation registry payload exceeds the stored bound")
    identity = _canonical_json(
        {
            "artifactHash": artifact_hash,
            "observedAt": observed_at.isoformat(),
            "payload": payload,
            "schemaVersion": schema_version,
            "sourceVersion": source_version,
        }
    )
    identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return CorporationRegistryObservation(
        observation_id=f"cro_{identity_hash}",
        symbol=symbol,
        corp_code=corp_code,
        registry_status=str(registry_status),
        completeness=str(completeness),
        observed_at=observed_at,
        received_at=received_at,
        schema_version=schema_version,
        source_version=source_version,
        payload_json=payload_json,
        source_ref=hashlib.sha256(
            f"s1.6-corporation-registry:{identity_hash}".encode()
        ).hexdigest(),
        artifact_hash=artifact_hash,
    )


def _bounded_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ValueError(f"corporation registry {field} is invalid")
    return value


def _aware_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"corporation registry {field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"corporation registry {field} is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError(f"corporation registry {field} must include an offset")
    return parsed


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
