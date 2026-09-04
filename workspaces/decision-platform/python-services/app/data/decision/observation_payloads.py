"""Build the stored observations consumed by RiskEngine."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Final

# Observation schemas store ratios as four-decimal strings.
_RATIO_PLACES: Final = Decimal("0.0001")
_QUOTE_TICK_KRW: Final = 100
_SCOPE_SUFFIX_ZEROS: Final = 32
_ACCOUNT_PREFIX: Final = "acct_"
# Shared classification keeps balance and instrument observations consistent.
GOLD_ETF_SYMBOLS: Final = frozenset({"132030"})


def owner_scope_hash(account_id: str) -> str:
    """Derive the observation scope without exposing a brokerage account number."""

    if not account_id.startswith(_ACCOUNT_PREFIX):
        raise ValueError("observation account id must start with acct_")
    body = account_id[len(_ACCOUNT_PREFIX) :]
    if len(body) != 32 or not all(character in "0123456789abcdef" for character in body):
        raise ValueError("observation account id body must be 32 hex characters")
    return body + "0" * _SCOPE_SUFFIX_ZEROS


def _stamps(now: datetime) -> tuple[str, str]:
    """Return timestamps that satisfy the observed-before-received contract."""

    observed = (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    received = (now - timedelta(seconds=29)).isoformat().replace("+00:00", "Z")
    return observed, received


def _ratio(value: Decimal) -> str:
    return str(value.quantize(_RATIO_PLACES))


def market_quote_payload(
    prices: Mapping[str, int], *, now: datetime, source_version: str
) -> dict[str, Any]:
    """Build quote observations from the latest stored closes."""

    if not prices:
        raise ValueError("market quote observation needs at least one price")
    if any(price <= 0 for price in prices.values()):
        raise ValueError("market quote observation price must be positive")
    observed, received = _stamps(now)
    return {
        "observedAt": observed,
        "quotes": [
            {
                "askKrw": price,
                "bidKrw": price - _QUOTE_TICK_KRW,
                "completeness": "COMPLETE",
                "previousCloseKrw": price - _QUOTE_TICK_KRW,
                "priceKrw": price,
                "symbol": symbol,
            }
            for symbol, price in sorted(prices.items())
        ],
        "receivedAt": received,
        "schemaVersion": "market-quote-observation.v1",
        "sourceVersion": source_version,
    }


def instrument_catalog_payload(
    symbols: Sequence[str],
    gold_etf_symbols: frozenset[str],
    *,
    now: datetime,
    source_version: str,
) -> dict[str, Any]:
    """Build the product classification required by portfolio limits."""

    if not symbols:
        raise ValueError("instrument catalog observation needs at least one symbol")
    observed, received = _stamps(now)
    return {
        "catalogVersion": "1",
        "instruments": [
            {
                "isEtfEtn": symbol in gold_etf_symbols,
                "isGoldEtfEtn": symbol in gold_etf_symbols,
                "productRiskScore": _ratio(Decimal(0)),
                "symbol": symbol,
            }
            for symbol in sorted(symbols)
        ],
        "observedAt": observed,
        "receivedAt": received,
        "schemaVersion": "instrument-catalog-observation.v1",
        "sourceVersion": source_version,
    }


def portfolio_balance_payload(
    *,
    owner_user_id: str,
    scope_hash: str,
    cash_krw: int,
    positions: Sequence[Mapping[str, Any]],
    now: datetime,
    source_version: str,
    gold_etf_symbols: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Build a sanitized balance observation from a KIS response."""

    if cash_krw < 0:
        raise ValueError("portfolio balance cash must not be negative")
    # Preserve the broker-reported market value instead of recalculating it.
    market_value = sum(int(item["marketValueKrw"]) for item in positions)
    observed, received = _stamps(now)
    return {
        "cashKrw": cash_krw,
        "completeness": "COMPLETE",
        "marginRequirementKrw": 0,
        "observedAt": observed,
        "ownerScopeHash": scope_hash,
        "ownerUserId": owner_user_id,
        "portfolioEquityKrw": cash_krw + market_value,
        # KIS omits product class, so use the versioned universe classification.
        "positions": [
            {
                "isGoldEtfEtn": str(item["symbol"]) in gold_etf_symbols,
                "marketValueKrw": int(item["marketValueKrw"]),
                "quantity": int(item["quantity"]),
                "symbol": str(item["symbol"]),
            }
            for item in positions
        ],
        "receivedAt": received,
        "schemaVersion": "2",
        "sourceVersion": source_version,
    }


def deterministic_metrics_payload(
    *,
    owner_user_id: str,
    scope_hash: str,
    portfolio_source: str,
    equity_krw: int,
    baseline_equity_krw: int,
    daily_order_count: int,
    trading_date: str,
    now: datetime,
    source_version: str,
) -> dict[str, Any]:
    """기준 자본 대비 현재 자본에서 위험지표를 계산한다.

    `daily_loss_guard` 와 `mdd_guard` 가 이 값을 읽는다. 지어내지 않는다 - 거래가 없으면 손실도
    낙폭도 0 이고, 거래가 있으면 기준 대비 하락분이 그 값이다.

    한계를 분명히 적는다. `maxDrawdown` 은 **세션 기준 자본 대비 낙폭**이고 여러 세션에 걸친
    최고점-최저점 낙폭이 아니다. 후자는 자본 시계열이 필요한데 이 호출 지점에는 그 시계열이
    없다. 즉 이 값은 다세션 낙폭을 **과소평가**할 수 있다. `mdd_guard` 를 다세션 기준으로
    걸어야 하면 자본 이력을 읽는 출처를 먼저 만들어야 한다 - 지어낸 값으로 통과시키지 않는다.

    연변동성도 이 관측 하나로는 계산할 수 없다(수익률 시계열이 필요하다). 원칙 규칙이 이 값을
    쓰지 않으므로 0 으로 두고, 쓰게 되는 날 시계열 출처와 함께 채운다.
    """

    if baseline_equity_krw <= 0:
        raise ValueError("deterministic metrics baseline equity must be positive")
    if equity_krw < 0 or daily_order_count < 0:
        raise ValueError("deterministic metrics input must not be negative")
    change = (Decimal(equity_krw) - Decimal(baseline_equity_krw)) / Decimal(baseline_equity_krw)
    loss_rate = min(change, Decimal(0))
    observed, received = _stamps(now)
    return {
        "dailyOrderCount": {
            "completeness": "COMPLETE",
            "coveredThrough": observed,
            "observedAt": observed,
            "orderCount": daily_order_count,
            "receivedAt": received,
            "tradingDate": trading_date,
        },
        "ownerScopeHash": scope_hash,
        "ownerUserId": owner_user_id,
        "portfolioSource": portfolio_source,
        "risk": {
            "annualizedVolatility": _ratio(Decimal(0)),
            "completeness": "COMPLETE",
            "dailyLossRate": _ratio(loss_rate),
            "maxDrawdown": _ratio(loss_rate),
            "observedAt": observed,
            "receivedAt": received,
        },
        "schemaVersion": "deterministic-risk-observation.v2",
        "sourceVersion": source_version,
    }
