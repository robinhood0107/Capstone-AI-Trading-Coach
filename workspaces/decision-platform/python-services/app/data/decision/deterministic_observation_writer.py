"""deterministic risk와 daily order-count fixture를 append-only Decision source로 적재한다."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import psycopg

from app.offline_fixture_io import read_json_fixture

_ROOT_FIELDS = {
    "schemaVersion",
    "sourceVersion",
    "ownerUserId",
    "ownerScopeHash",
    "portfolioSource",
    "risk",
    "dailyOrderCount",
}
_RISK_FIELDS = {
    "observedAt",
    "receivedAt",
    "dailyLossRate",
    "maxDrawdown",
    "annualizedVolatility",
    "completeness",
}
_ORDER_FIELDS = {
    "tradingDate",
    "orderCount",
    "coveredThrough",
    "observedAt",
    "receivedAt",
    "completeness",
}
_OWNER_USER_ID = re.compile(r"^[0-9A-Za-z._:-]{1,128}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_PAYLOAD_BYTES = 262_144
_MAX_FIXTURE_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class DeterministicRiskObservation:
    """previous-session provenance와 세 지표를 함께 고정하는 immutable observation이다."""

    observation_id: str
    daily_loss_rate: Decimal | None
    max_drawdown: Decimal | None
    annualized_volatility: Decimal | None
    completeness: str
    observed_at: datetime
    received_at: datetime
    payload_json: str
    source_ref: str


@dataclass(frozen=True, slots=True)
class DailyOrderCountObservation:
    """evaluationAsOf coverage를 명시해 authoritative 0과 결측을 구분하는 observation이다."""

    observation_id: str
    trading_date: date
    order_count: int | None
    covered_through: datetime
    completeness: str
    observed_at: datetime
    received_at: datetime
    payload_json: str
    source_ref: str


@dataclass(frozen=True, slots=True)
class DeterministicMetricFixture:
    """두 observation이 공유하는 owner/source/version/artifact 경계를 표현한다."""

    owner_user_id: str
    owner_scope_hash: str
    portfolio_source: str
    schema_version: str
    source_version: str
    artifact_hash: str
    risk: DeterministicRiskObservation
    daily_order_count: DailyOrderCountObservation


def load_deterministic_metric_fixture(path: Path) -> DeterministicMetricFixture:
    """local deterministic fixture만 검증하며 broker/provider 호출이나 0 fallback을 수행하지 않는다."""
    artifact_bytes, root = read_json_fixture(
        path,
        max_bytes=_MAX_FIXTURE_BYTES,
        label="deterministic metric",
    )
    artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()
    if not isinstance(root, dict) or set(root) != _ROOT_FIELDS:
        raise ValueError("deterministic metric fixture root shape is invalid")
    schema_version = _bounded_text(root["schemaVersion"], "schemaVersion")
    source_version = _bounded_text(root["sourceVersion"], "sourceVersion")
    owner_user_id = root["ownerUserId"]
    if not isinstance(owner_user_id, str) or _OWNER_USER_ID.fullmatch(owner_user_id) is None:
        raise ValueError("deterministic metric ownerUserId is invalid")
    owner_scope_hash = root["ownerScopeHash"]
    if not isinstance(owner_scope_hash, str) or _SHA256.fullmatch(owner_scope_hash) is None:
        raise ValueError("deterministic metric ownerScopeHash is invalid")
    portfolio_source = root["portfolioSource"]
    if portfolio_source not in {"KIS_MOCK", "INTERNAL_PAPER"}:
        raise ValueError("deterministic metric portfolioSource is invalid")

    risk = _risk_observation(
        root["risk"],
        schema_version=schema_version,
        source_version=source_version,
        owner_scope_hash=owner_scope_hash,
        portfolio_source=portfolio_source,
        artifact_hash=artifact_hash,
    )
    daily_order_count = _order_observation(
        root["dailyOrderCount"],
        schema_version=schema_version,
        source_version=source_version,
        owner_scope_hash=owner_scope_hash,
        portfolio_source=portfolio_source,
        artifact_hash=artifact_hash,
    )
    return DeterministicMetricFixture(
        owner_user_id=owner_user_id,
        owner_scope_hash=owner_scope_hash,
        portfolio_source=portfolio_source,
        schema_version=schema_version,
        source_version=source_version,
        artifact_hash=artifact_hash,
        risk=risk,
        daily_order_count=daily_order_count,
    )


def append_deterministic_metric_fixture(path: Path, *, database_dsn: str) -> int:
    """decision_risk_writer로 risk/order-count를 한 transaction에서 exact INSERT한다."""
    if not database_dsn.strip():
        raise ValueError("decision risk writer database DSN is required")
    fixture = load_deterministic_metric_fixture(path)
    with psycopg.connect(database_dsn, autocommit=False) as connection:
        with connection.transaction():
            risk = fixture.risk
            inserted_risk = connection.execute(
                """
                INSERT INTO deterministic_risk_observations (
                  observation_id, owner_user_id, owner_scope_hash, portfolio_source,
                  daily_loss_rate, max_drawdown, annualized_volatility, completeness,
                  observed_at, received_at, schema_version, source_version,
                  payload_json, source_ref, artifact_hash
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s::jsonb, %s, %s
                )
                ON CONFLICT DO NOTHING
                """,
                (
                    risk.observation_id,
                    fixture.owner_user_id,
                    fixture.owner_scope_hash,
                    fixture.portfolio_source,
                    risk.daily_loss_rate,
                    risk.max_drawdown,
                    risk.annualized_volatility,
                    risk.completeness,
                    risk.observed_at,
                    risk.received_at,
                    fixture.schema_version,
                    fixture.source_version,
                    risk.payload_json,
                    risk.source_ref,
                    fixture.artifact_hash,
                ),
            ).rowcount
            orders = fixture.daily_order_count
            inserted_orders = connection.execute(
                """
                INSERT INTO daily_order_count_observations (
                  observation_id, owner_user_id, owner_scope_hash, portfolio_source,
                  trading_date, order_count, covered_through, completeness,
                  observed_at, received_at, schema_version, source_version,
                  payload_json, source_ref, artifact_hash
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s::jsonb, %s, %s
                )
                ON CONFLICT DO NOTHING
                """,
                (
                    orders.observation_id,
                    fixture.owner_user_id,
                    fixture.owner_scope_hash,
                    fixture.portfolio_source,
                    orders.trading_date,
                    orders.order_count,
                    orders.covered_through,
                    orders.completeness,
                    orders.observed_at,
                    orders.received_at,
                    fixture.schema_version,
                    fixture.source_version,
                    orders.payload_json,
                    orders.source_ref,
                    fixture.artifact_hash,
                ),
            ).rowcount
    return inserted_risk + inserted_orders


def _risk_observation(
    value: object,
    *,
    schema_version: str,
    source_version: str,
    owner_scope_hash: str,
    portfolio_source: str,
    artifact_hash: str,
) -> DeterministicRiskObservation:
    if not isinstance(value, dict) or set(value) != _RISK_FIELDS:
        raise ValueError("deterministic risk shape is invalid")
    observed_at = _aware_datetime(value["observedAt"], "risk.observedAt")
    received_at = _aware_datetime(value["receivedAt"], "risk.receivedAt")
    if received_at < observed_at:
        raise ValueError("deterministic risk receivedAt precedes observedAt")
    completeness = _completeness(value["completeness"], "risk")
    daily_loss_rate = _optional_decimal(value["dailyLossRate"], "dailyLossRate", Decimal("-1"), Decimal("0"))
    max_drawdown = _optional_decimal(value["maxDrawdown"], "maxDrawdown", Decimal("-1"), Decimal("0"))
    annualized_volatility = _optional_decimal(
        value["annualizedVolatility"],
        "annualizedVolatility",
        Decimal("0"),
        Decimal("9.999999999999999999"),
    )
    values = (daily_loss_rate, max_drawdown, annualized_volatility)
    if completeness == "COMPLETE" and any(item is None for item in values):
        raise ValueError("complete deterministic risk requires all metric values")
    payload = {
        "annualizedVolatility": _decimal_text(annualized_volatility),
        "completeness": completeness,
        "dailyLossRate": _decimal_text(daily_loss_rate),
        "maxDrawdown": _decimal_text(max_drawdown),
        "ownerScopeHash": owner_scope_hash,
        "portfolioSource": portfolio_source,
    }
    payload_json = _bounded_payload(payload, "deterministic risk")
    identity_hash = _identity_hash(
        payload=payload,
        observed_at=observed_at,
        schema_version=schema_version,
        source_version=source_version,
        artifact_hash=artifact_hash,
    )
    return DeterministicRiskObservation(
        observation_id=f"dro_{identity_hash}",
        daily_loss_rate=daily_loss_rate,
        max_drawdown=max_drawdown,
        annualized_volatility=annualized_volatility,
        completeness=completeness,
        observed_at=observed_at,
        received_at=received_at,
        payload_json=payload_json,
        source_ref=hashlib.sha256(f"deterministic-risk:{identity_hash}".encode()).hexdigest(),
    )


def _order_observation(
    value: object,
    *,
    schema_version: str,
    source_version: str,
    owner_scope_hash: str,
    portfolio_source: str,
    artifact_hash: str,
) -> DailyOrderCountObservation:
    if not isinstance(value, dict) or set(value) != _ORDER_FIELDS:
        raise ValueError("daily order-count shape is invalid")
    trading_date = _date(value["tradingDate"])
    order_count = _optional_order_count(value["orderCount"])
    covered_through = _aware_datetime(value["coveredThrough"], "dailyOrderCount.coveredThrough")
    observed_at = _aware_datetime(value["observedAt"], "dailyOrderCount.observedAt")
    received_at = _aware_datetime(value["receivedAt"], "dailyOrderCount.receivedAt")
    if covered_through > observed_at or received_at < observed_at:
        raise ValueError("daily order-count time boundary is invalid")
    completeness = _completeness(value["completeness"], "dailyOrderCount")
    if completeness == "COMPLETE" and order_count is None:
        raise ValueError("complete daily order-count requires an authoritative count")
    payload = {
        "completeness": completeness,
        "coveredThrough": covered_through.isoformat(),
        "orderCount": order_count,
        "ownerScopeHash": owner_scope_hash,
        "portfolioSource": portfolio_source,
        "tradingDate": trading_date.isoformat(),
    }
    payload_json = _bounded_payload(payload, "daily order-count")
    identity_hash = _identity_hash(
        payload=payload,
        observed_at=observed_at,
        schema_version=schema_version,
        source_version=source_version,
        artifact_hash=artifact_hash,
    )
    return DailyOrderCountObservation(
        observation_id=f"doc_{identity_hash}",
        trading_date=trading_date,
        order_count=order_count,
        covered_through=covered_through,
        completeness=completeness,
        observed_at=observed_at,
        received_at=received_at,
        payload_json=payload_json,
        source_ref=hashlib.sha256(f"daily-order-count:{identity_hash}".encode()).hexdigest(),
    )


def _identity_hash(
    *,
    payload: dict[str, Any],
    observed_at: datetime,
    schema_version: str,
    source_version: str,
    artifact_hash: str,
) -> str:
    identity = _canonical_json(
        {
            "artifactHash": artifact_hash,
            "observedAt": observed_at.isoformat(),
            "payload": payload,
            "schemaVersion": schema_version,
            "sourceVersion": source_version,
        }
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _optional_decimal(
    value: object,
    field: str,
    minimum: Decimal,
    maximum: Decimal,
) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"deterministic metric {field} is invalid")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(f"deterministic metric {field} is invalid") from error
    exponent = parsed.as_tuple().exponent
    if (
        not parsed.is_finite()
        or parsed < minimum
        or parsed > maximum
        or not isinstance(exponent, int)
        or exponent < -18
    ):
        raise ValueError(f"deterministic metric {field} is out of range")
    return parsed


def _optional_order_count(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 1_000_000:
        raise ValueError("daily order-count value is invalid")
    return value


def _bounded_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ValueError(f"deterministic metric {field} is invalid")
    return value


def _aware_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"deterministic metric {field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"deterministic metric {field} is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError(f"deterministic metric {field} must include an offset")
    return parsed


def _date(value: object) -> date:
    if not isinstance(value, str):
        raise ValueError("daily order-count tradingDate is invalid")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("daily order-count tradingDate is invalid") from error


def _completeness(value: object, field: str) -> str:
    if value not in {"COMPLETE", "PARTIAL"}:
        raise ValueError(f"{field} completeness is invalid")
    return str(value)


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _bounded_payload(value: dict[str, Any], field: str) -> str:
    encoded = _canonical_json(value)
    if len(encoded.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError(f"{field} payload exceeds the stored bound")
    return encoded


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
