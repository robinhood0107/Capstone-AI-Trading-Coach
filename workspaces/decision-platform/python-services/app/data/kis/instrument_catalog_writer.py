"""S1.1 sanitized local instrument catalog를 append-only Decision source로 적재한다."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import psycopg

from app.offline_fixture_io import read_json_fixture

_ROOT_FIELDS = {
    "schemaVersion",
    "catalogVersion",
    "sourceVersion",
    "observedAt",
    "receivedAt",
    "instruments",
}
_INSTRUMENT_FIELDS = {
    "symbol",
    "isEtfEtn",
    "isGoldEtfEtn",
    "productRiskScore",
}
_SYMBOL = re.compile(r"^[0-9A-Z._:-]{1,20}$")
_MAX_INSTRUMENTS = 10_000
_MAX_FIXTURE_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class InstrumentCatalogObservation:
    """정제 fixture 한 행의 exact typed contract이며 raw provider/account field를 허용하지 않는다."""

    observation_id: str
    symbol: str
    is_etf_etn: bool
    is_gold_etf_etn: bool
    product_risk_score: Decimal | None
    catalog_version: str
    observed_at: datetime
    received_at: datetime
    schema_version: str
    source_version: str
    payload_json: str
    source_ref: str
    artifact_hash: str


def load_instrument_catalog_fixture(path: Path) -> tuple[InstrumentCatalogObservation, ...]:
    """versioned local JSON만 읽고 canonical row/hash를 만들며 network나 fallback은 사용하지 않는다."""
    artifact_bytes, root = read_json_fixture(
        path,
        max_bytes=_MAX_FIXTURE_BYTES,
        label="instrument catalog",
    )
    artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()
    if not isinstance(root, dict) or set(root) != _ROOT_FIELDS:
        raise ValueError("instrument catalog fixture root shape is invalid")
    schema_version = _bounded_text(root["schemaVersion"], "schemaVersion")
    catalog_version = _bounded_text(root["catalogVersion"], "catalogVersion")
    source_version = _bounded_text(root["sourceVersion"], "sourceVersion")
    observed_at = _aware_datetime(root["observedAt"], "observedAt")
    received_at = _aware_datetime(root["receivedAt"], "receivedAt")
    if received_at < observed_at:
        raise ValueError("instrument catalog receivedAt precedes observedAt")
    instruments = root["instruments"]
    if not isinstance(instruments, list) or not 1 <= len(instruments) <= _MAX_INSTRUMENTS:
        raise ValueError("instrument catalog fixture size is invalid")

    observations = tuple(
        _observation(
            item,
            schema_version=schema_version,
            catalog_version=catalog_version,
            source_version=source_version,
            observed_at=observed_at,
            received_at=received_at,
            artifact_hash=artifact_hash,
        )
        for item in instruments
    )
    symbols = [observation.symbol for observation in observations]
    if len(symbols) != len(set(symbols)):
        raise ValueError("instrument catalog fixture contains duplicate symbols")
    return tuple(sorted(observations, key=lambda observation: observation.symbol))


def append_instrument_catalog_fixture(path: Path, *, database_dsn: str) -> int:
    """decision_market_writer DSN으로 exact INSERT만 수행하고 동일 fixture replay는 no-op으로 만든다."""
    if not database_dsn.strip():
        raise ValueError("decision market writer database DSN is required")
    observations = load_instrument_catalog_fixture(path)
    inserted = 0
    with psycopg.connect(database_dsn, autocommit=False) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO instrument_catalog_observations (
                      observation_id, symbol, is_etf_etn, is_gold_etf_etn,
                      product_risk_score, catalog_version, observed_at, received_at,
                      completeness, schema_version, source_version, payload_json,
                      source_ref, artifact_hash
                    ) VALUES (
                      %s, %s, %s, %s, %s, %s, %s, %s,
                      'COMPLETE', %s, %s, %s::jsonb, %s, %s
                    )
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        (
                            observation.observation_id,
                            observation.symbol,
                            observation.is_etf_etn,
                            observation.is_gold_etf_etn,
                            observation.product_risk_score,
                            observation.catalog_version,
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
    catalog_version: str,
    source_version: str,
    observed_at: datetime,
    received_at: datetime,
    artifact_hash: str,
) -> InstrumentCatalogObservation:
    if not isinstance(value, dict) or set(value) != _INSTRUMENT_FIELDS:
        raise ValueError("instrument catalog item shape is invalid")
    symbol_value = value["symbol"]
    if not isinstance(symbol_value, str) or _SYMBOL.fullmatch(symbol_value) is None:
        raise ValueError("instrument catalog symbol is invalid")
    is_etf_etn = _exact_bool(value["isEtfEtn"], "isEtfEtn")
    is_gold_etf_etn = _exact_bool(value["isGoldEtfEtn"], "isGoldEtfEtn")
    if is_gold_etf_etn and not is_etf_etn:
        raise ValueError("gold ETF/ETN must also be classified as ETF/ETN")
    product_risk_score = _risk_score(value["productRiskScore"])
    payload = {
        "catalogVersion": catalog_version,
        "isEtfEtn": is_etf_etn,
        "isGoldEtfEtn": is_gold_etf_etn,
        "productRiskScore": (
            None if product_risk_score is None else format(product_risk_score, "f")
        ),
        "symbol": symbol_value,
    }
    payload_json = _canonical_json(payload)
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
    source_ref = hashlib.sha256(f"s1.1-instrument-source:{identity_hash}".encode()).hexdigest()
    return InstrumentCatalogObservation(
        observation_id=f"ins_{identity_hash}",
        symbol=symbol_value,
        is_etf_etn=is_etf_etn,
        is_gold_etf_etn=is_gold_etf_etn,
        product_risk_score=product_risk_score,
        catalog_version=catalog_version,
        observed_at=observed_at,
        received_at=received_at,
        schema_version=schema_version,
        source_version=source_version,
        payload_json=payload_json,
        source_ref=source_ref,
        artifact_hash=artifact_hash,
    )


def _bounded_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ValueError(f"instrument catalog {field} is invalid")
    return value


def _aware_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"instrument catalog {field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"instrument catalog {field} is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError(f"instrument catalog {field} must include an offset")
    return parsed


def _exact_bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"instrument catalog {field} must be boolean")
    return value


def _risk_score(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("instrument catalog productRiskScore is invalid")
    try:
        score = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("instrument catalog productRiskScore is invalid") from error
    if not score.is_finite() or score < 0 or score > 1:
        raise ValueError("instrument catalog productRiskScore is out of range")
    exponent = score.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -8:
        raise ValueError("instrument catalog productRiskScore scale exceeds 8")
    return score


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
