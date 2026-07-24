"""S1.1 sanitized current quote fixture를 append-only Decision source로 적재한다."""

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
    "quotes",
}
_QUOTE_FIELDS = {
    "symbol",
    "priceKrw",
    "bidKrw",
    "askKrw",
    "completeness",
}
_SYMBOL = re.compile(r"^[0-9A-Z._:-]{1,20}$")
_MAX_QUOTES = 10_000
_MAX_PAYLOAD_BYTES = 65_536
_MAX_FIXTURE_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class MarketQuoteObservation:
    """provider header/token을 제외한 현재가·호가의 exact stored-observation 계약이다."""

    observation_id: str
    symbol: str
    price_krw: int
    bid_krw: int | None
    ask_krw: int | None
    completeness: str
    observed_at: datetime
    received_at: datetime
    schema_version: str
    source_version: str
    payload_json: str
    source_ref: str
    artifact_hash: str


def load_market_quote_fixture(path: Path) -> tuple[MarketQuoteObservation, ...]:
    """versioned local JSON만 읽고 network·provider·production fallback 없이 observation을 만든다."""
    artifact_bytes = read_bounded_fixture(
        path,
        max_bytes=_MAX_FIXTURE_BYTES,
        label="market quote",
    )
    artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()
    root = json.loads(artifact_bytes)
    if not isinstance(root, dict) or set(root) != _ROOT_FIELDS:
        raise ValueError("market quote fixture root shape is invalid")
    schema_version = _bounded_text(root["schemaVersion"], "schemaVersion")
    source_version = _bounded_text(root["sourceVersion"], "sourceVersion")
    observed_at = _aware_datetime(root["observedAt"], "observedAt")
    received_at = _aware_datetime(root["receivedAt"], "receivedAt")
    if received_at < observed_at:
        raise ValueError("market quote receivedAt precedes observedAt")
    quotes = root["quotes"]
    if not isinstance(quotes, list) or not 1 <= len(quotes) <= _MAX_QUOTES:
        raise ValueError("market quote fixture size is invalid")

    observations = tuple(
        _observation(
            quote,
            schema_version=schema_version,
            source_version=source_version,
            observed_at=observed_at,
            received_at=received_at,
            artifact_hash=artifact_hash,
        )
        for quote in quotes
    )
    symbols = [observation.symbol for observation in observations]
    if len(symbols) != len(set(symbols)):
        raise ValueError("market quote fixture contains duplicate symbols")
    return tuple(sorted(observations, key=lambda observation: observation.symbol))


def append_market_quote_fixture(path: Path, *, database_dsn: str) -> int:
    """decision_market_writer DSN으로 exact INSERT만 수행하고 동일 observation은 no-op 처리한다."""
    if not database_dsn.strip():
        raise ValueError("decision market writer database DSN is required")
    observations = load_market_quote_fixture(path)
    inserted = 0
    with psycopg.connect(database_dsn, autocommit=False) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO market_quote_observations (
                      observation_id, symbol, source, price_krw, bid_krw, ask_krw,
                      completeness, observed_at, received_at, schema_version,
                      source_version, payload_json, source_ref, artifact_hash
                    ) VALUES (
                      %s, %s, 'KIS_MOCK', %s, %s, %s,
                      %s, %s, %s, %s, %s, %s::jsonb, %s, %s
                    )
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        (
                            observation.observation_id,
                            observation.symbol,
                            observation.price_krw,
                            observation.bid_krw,
                            observation.ask_krw,
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
) -> MarketQuoteObservation:
    if not isinstance(value, dict) or set(value) != _QUOTE_FIELDS:
        raise ValueError("market quote item shape is invalid")
    symbol = value["symbol"]
    if not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None:
        raise ValueError("market quote symbol is invalid")
    price_krw = _positive_int(value["priceKrw"], "priceKrw")
    bid_krw = _optional_positive_int(value["bidKrw"], "bidKrw")
    ask_krw = _optional_positive_int(value["askKrw"], "askKrw")
    if bid_krw is not None and ask_krw is not None and bid_krw > ask_krw:
        raise ValueError("market quote bid exceeds ask")
    completeness = value["completeness"]
    if completeness not in {"COMPLETE", "PARTIAL"}:
        raise ValueError("market quote completeness is invalid")
    if completeness == "COMPLETE" and (bid_krw is None or ask_krw is None):
        raise ValueError("complete market quote requires bounded orderbook prices")

    payload = {
        "askKrw": ask_krw,
        "bidKrw": bid_krw,
        "completeness": completeness,
        "priceKrw": price_krw,
        "symbol": symbol,
    }
    payload_json = _canonical_json(payload)
    if len(payload_json.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError("market quote payload exceeds the stored bound")
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
    return MarketQuoteObservation(
        observation_id=f"mqo_{identity_hash}",
        symbol=symbol,
        price_krw=price_krw,
        bid_krw=bid_krw,
        ask_krw=ask_krw,
        completeness=completeness,
        observed_at=observed_at,
        received_at=received_at,
        schema_version=schema_version,
        source_version=source_version,
        payload_json=payload_json,
        source_ref=hashlib.sha256(f"s1.1-market-quote:{identity_hash}".encode()).hexdigest(),
        artifact_hash=artifact_hash,
    )


def _bounded_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ValueError(f"market quote {field} is invalid")
    return value


def _aware_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"market quote {field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"market quote {field} is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError(f"market quote {field} must include an offset")
    return parsed


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value <= 0 or value > 9_223_372_036_854_775_807:
        raise ValueError(f"market quote {field} is invalid")
    return value


def _optional_positive_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, field)


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
