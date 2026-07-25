"""S3 KIS_MOCK balance/position fixture를 owner-scoped append-only source로 적재한다."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg

from app.offline_fixture_io import read_json_fixture

_ROOT_FIELDS = {
    "schemaVersion",
    "sourceVersion",
    "ownerUserId",
    "ownerScopeHash",
    "observedAt",
    "receivedAt",
    "completeness",
    "cashKrw",
    "portfolioEquityKrw",
    "marginRequirementKrw",
    "positions",
}
_POSITION_FIELDS = {
    "symbol",
    "quantity",
    "marketValueKrw",
    "isGoldEtfEtn",
}
_SYMBOL = re.compile(r"^[0-9A-Z._:-]{1,20}$")
_OWNER_USER_ID = re.compile(r"^[0-9A-Za-z._:-]{1,128}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_POSITIONS = 1_000
_MAX_PAYLOAD_BYTES = 262_144
_BIGINT_MAX = 9_223_372_036_854_775_807
_MAX_FIXTURE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class KisMockPositionObservation:
    """raw account identifier 없이 저장 가능한 한 종목의 sanitized position 계약이다."""

    symbol: str
    quantity: int
    market_value_krw: int
    is_gold_etf_etn: bool


@dataclass(frozen=True, slots=True)
class KisMockPortfolioObservation:
    """JWT owner와 HMAC scope만 사용하는 KIS_MOCK balance/position observation이다."""

    observation_id: str
    owner_user_id: str
    owner_scope_hash: str
    cash_krw: int
    portfolio_equity_krw: int
    margin_requirement_krw: int
    completeness: str
    positions: tuple[KisMockPositionObservation, ...]
    observed_at: datetime
    received_at: datetime
    schema_version: str
    source_version: str
    payload_json: str
    source_ref: str
    artifact_hash: str


def load_kis_mock_portfolio_fixture(path: Path) -> KisMockPortfolioObservation:
    """versioned sanitized fixture만 읽으며 account number나 provider 응답은 입력으로 받지 않는다."""
    artifact_bytes, root = read_json_fixture(
        path,
        max_bytes=_MAX_FIXTURE_BYTES,
        label="KIS_MOCK portfolio",
    )
    artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()
    if not isinstance(root, dict) or set(root) != _ROOT_FIELDS:
        raise ValueError("KIS_MOCK portfolio fixture root shape is invalid")
    schema_version = _bounded_text(root["schemaVersion"], "schemaVersion")
    source_version = _bounded_text(root["sourceVersion"], "sourceVersion")
    owner_user_id = root["ownerUserId"]
    if not isinstance(owner_user_id, str) or _OWNER_USER_ID.fullmatch(owner_user_id) is None:
        raise ValueError("KIS_MOCK portfolio ownerUserId is invalid")
    owner_scope_hash = root["ownerScopeHash"]
    if not isinstance(owner_scope_hash, str) or _SHA256.fullmatch(owner_scope_hash) is None:
        raise ValueError("KIS_MOCK portfolio ownerScopeHash is invalid")
    observed_at = _aware_datetime(root["observedAt"], "observedAt")
    received_at = _aware_datetime(root["receivedAt"], "receivedAt")
    if received_at < observed_at:
        raise ValueError("KIS_MOCK portfolio receivedAt precedes observedAt")
    completeness = root["completeness"]
    if completeness not in {"COMPLETE", "PARTIAL"}:
        raise ValueError("KIS_MOCK portfolio completeness is invalid")
    cash_krw = _nonnegative_bigint(root["cashKrw"], "cashKrw")
    portfolio_equity_krw = _nonnegative_bigint(
        root["portfolioEquityKrw"],
        "portfolioEquityKrw",
    )
    margin_requirement_krw = _nonnegative_bigint(
        root["marginRequirementKrw"],
        "marginRequirementKrw",
    )
    raw_positions = root["positions"]
    if not isinstance(raw_positions, list) or len(raw_positions) > _MAX_POSITIONS:
        raise ValueError("KIS_MOCK portfolio position size is invalid")
    positions = tuple(sorted((_position(value) for value in raw_positions), key=lambda row: row.symbol))
    if len({position.symbol for position in positions}) != len(positions):
        raise ValueError("KIS_MOCK portfolio contains duplicate symbols")

    payload = {
        "cashKrw": cash_krw,
        "completeness": completeness,
        "marginRequirementKrw": margin_requirement_krw,
        "ownerScopeHash": owner_scope_hash,
        "portfolioEquityKrw": portfolio_equity_krw,
        "positions": [
            {
                "isGoldEtfEtn": position.is_gold_etf_etn,
                "marketValueKrw": position.market_value_krw,
                "quantity": position.quantity,
                "symbol": position.symbol,
            }
            for position in positions
        ],
    }
    payload_json = _canonical_json(payload)
    if len(payload_json.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError("KIS_MOCK portfolio payload exceeds the stored bound")
    identity = _canonical_json(
        {
            "artifactHash": artifact_hash,
            "observedAt": observed_at.isoformat(),
            "ownerScopeHash": owner_scope_hash,
            "payload": payload,
            "schemaVersion": schema_version,
            "sourceVersion": source_version,
        }
    )
    identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return KisMockPortfolioObservation(
        observation_id=f"pbo_{identity_hash}",
        owner_user_id=owner_user_id,
        owner_scope_hash=owner_scope_hash,
        cash_krw=cash_krw,
        portfolio_equity_krw=portfolio_equity_krw,
        margin_requirement_krw=margin_requirement_krw,
        completeness=completeness,
        positions=positions,
        observed_at=observed_at,
        received_at=received_at,
        schema_version=schema_version,
        source_version=source_version,
        payload_json=payload_json,
        source_ref=hashlib.sha256(f"s3-kis-mock-portfolio:{identity_hash}".encode()).hexdigest(),
        artifact_hash=artifact_hash,
    )


def append_kis_mock_portfolio_fixture(path: Path, *, database_dsn: str) -> int:
    """decision_portfolio_writer로 parent/positions를 한 transaction에서 exact INSERT한다."""
    if not database_dsn.strip():
        raise ValueError("decision portfolio writer database DSN is required")
    observation = load_kis_mock_portfolio_fixture(path)
    with psycopg.connect(database_dsn, autocommit=False) as connection:
        with connection.transaction():
            inserted = connection.execute(
                """
                INSERT INTO portfolio_balance_observations (
                  observation_id, owner_user_id, account_scope_hash, source,
                  context_status, cash_krw, portfolio_equity_krw,
                  margin_requirement_krw, completeness, position_count,
                  observed_at, received_at, schema_version, source_version,
                  payload_json, source_ref, artifact_hash
                ) VALUES (
                  %s, %s, %s, 'KIS_MOCK',
                  'ACTIVE', %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s::jsonb, %s, %s
                )
                ON CONFLICT DO NOTHING
                """,
                (
                    observation.observation_id,
                    observation.owner_user_id,
                    observation.owner_scope_hash,
                    observation.cash_krw,
                    observation.portfolio_equity_krw,
                    observation.margin_requirement_krw,
                    observation.completeness,
                    len(observation.positions),
                    observation.observed_at,
                    observation.received_at,
                    observation.schema_version,
                    observation.source_version,
                    observation.payload_json,
                    observation.source_ref,
                    observation.artifact_hash,
                ),
            ).rowcount
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO portfolio_position_observations (
                      balance_observation_id, symbol, quantity,
                      market_value_krw, is_gold_etf_etn
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        (
                            observation.observation_id,
                            position.symbol,
                            position.quantity,
                            position.market_value_krw,
                            position.is_gold_etf_etn,
                        )
                        for position in observation.positions
                    ],
                )
    return inserted


def _position(value: object) -> KisMockPositionObservation:
    if not isinstance(value, dict) or set(value) != _POSITION_FIELDS:
        raise ValueError("KIS_MOCK portfolio position shape is invalid")
    symbol = value["symbol"]
    if not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None:
        raise ValueError("KIS_MOCK portfolio position symbol is invalid")
    classification = value["isGoldEtfEtn"]
    if type(classification) is not bool:
        raise ValueError("KIS_MOCK portfolio isGoldEtfEtn must be boolean")
    return KisMockPositionObservation(
        symbol=symbol,
        quantity=_nonnegative_bigint(value["quantity"], "quantity"),
        market_value_krw=_nonnegative_bigint(value["marketValueKrw"], "marketValueKrw"),
        is_gold_etf_etn=classification,
    )


def _bounded_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ValueError(f"KIS_MOCK portfolio {field} is invalid")
    return value


def _aware_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"KIS_MOCK portfolio {field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"KIS_MOCK portfolio {field} is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError(f"KIS_MOCK portfolio {field} must include an offset")
    return parsed


def _nonnegative_bigint(value: object, field: str) -> int:
    if type(value) is not int or value < 0 or value > _BIGINT_MAX:
        raise ValueError(f"KIS_MOCK portfolio {field} is invalid")
    return value


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
