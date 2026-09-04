"""RiskEngine 이 읽는 관측 페이로드를 만든다. 두 호출자가 같은 정의를 쓴다.

## 왜 이 모듈이 필요한가

자동운용이 RISK_CHECKING 에서 HOLD 로 닫혔다. 판정 응답의 `violations` 는 비어 있고 `issues`
가 전부 입력 부재였다.

    max_position_per_asset   BROKERAGE_UNAVAILABLE
    max_gold_etf_etn_weight  BROKERAGE_UNAVAILABLE
    max_single_order_amount  PRICE_MISSING
    daily_loss_guard / mdd   RISK_SNAPSHOT_MISSING

즉 원칙을 위반한 것이 아니라 평가 입력이 없어 fail-closed 된 것이다. RiskEngine 은 관측 표를
읽는데 그 표를 채우는 것은 운영자 CLI 뿐이었고 어디에도 배선돼 있지 않았다.

## 어디서 쓰는가 - 데이터를 가진 곳이 쓴다

    시세 · 종목카탈로그   일일 시장데이터 수집기. 가격이 그 컨테이너에 있다.
    잔고 · 위험지표       자동운용 런타임. KIS 잔고가 그 프로세스에 있다.

한 곳에서 넷을 다 쓰려 하면 그 컨테이너가 시장데이터 writer 와 KIS provider 를 동시에 갖게
된다. 그건 역할 분리를 무너뜨린다. 그래서 페이로드 정의만 공유하고 적재는 각자 한다.

## 신선도

`PreviousTradingDayFreshnessPolicy` 는 `observedAt >= 직전 개장일 장 마감` 이면 FRESH 이고
`freshUntil` 은 평가일 다음날 자정이다. 즉 직전 거래일 마감 이후 한 번 적재하면 당일 내내
유효하다. 하루 한 번으로 충분하고 장중 재적재가 필요하지 않다.

## 값을 지어내지 않는다

가격은 적재된 시장데이터의 마지막 종가, 잔고는 KIS 가 돌려준 현금과 보유, 위험지표는 기준
자본 대비 현재 자본에서 계산한다. ETF/금 여부는 커밋된 유니버스 카탈로그가 정한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Final

# 계약 automation-policy 와 무관한 표시 정밀도. 관측 스키마가 소수 넷을 문자열로 받는다.
_RATIO_PLACES: Final = Decimal("0.0001")
_QUOTE_TICK_KRW: Final = 100
_SCOPE_SUFFIX_ZEROS: Final = 32
_ACCOUNT_PREFIX: Final = "acct_"
# exact-31 중 금 ETF. 종목카탈로그와 잔고 관측이 같은 판정을 써야 `max_gold_etf_etn_weight`
# 규칙이 두 관측 사이에서 어긋나지 않는다. 그래서 두 호출자가 공유하는 이 모듈에 둔다.
GOLD_ETF_SYMBOLS: Final = frozenset({"132030"})


def owner_scope_hash(account_id: str) -> str:
    """계좌 식별자에서 소유 경계 해시를 만든다.

    관측 표와 RiskEngine 의 조회가 이 값으로 짝을 맞춘다. 계좌번호를 입력으로 두지 않는
    설계이므로 소유 경계는 계좌 식별자에서만 파생된다.
    """

    if not account_id.startswith(_ACCOUNT_PREFIX):
        raise ValueError("observation account id must start with acct_")
    body = account_id[len(_ACCOUNT_PREFIX) :]
    if len(body) != 32 or not all(character in "0123456789abcdef" for character in body):
        raise ValueError("observation account id body must be 32 hex characters")
    return body + "0" * _SCOPE_SUFFIX_ZEROS


def _stamps(now: datetime) -> tuple[str, str]:
    """관측 시각과 수신 시각. 계약이 `receivedAt >= observedAt` 을 요구한다."""

    observed = (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    received = (now - timedelta(seconds=29)).isoformat().replace("+00:00", "Z")
    return observed, received


def _ratio(value: Decimal) -> str:
    return str(value.quantize(_RATIO_PLACES))


def market_quote_payload(
    prices: Mapping[str, int], *, now: datetime, source_version: str
) -> dict[str, Any]:
    """적재된 마지막 종가를 시세 관측으로 만든다.

    RiskEngine 의 order_amount_krw 와 asset_weight 가 이 값을 읽는다. 호가는 격자 위에 있어야
    하므로 매수/매도 한 tick 을 벌린다.
    """

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
    """어느 종목이 ETF/ETN 이고 그중 금인지 알려준다.

    `max_gold_etf_etn_weight` 규칙이 이것 없이는 비중을 계산할 수 없어 BROKERAGE_UNAVAILABLE
    로 닫힌다. 판정 자체는 커밋된 유니버스 카탈로그가 하고 여기서 만들지 않는다.
    """

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
    """KIS 가 돌려준 현금과 보유를 잔고 관측으로 만든다.

    `asset_weight` 와 `gold_etf_etn_weight` 가 이 값을 읽는다. 계좌번호는 받지 않는다 -
    소유 경계는 scope hash 로만 표현한다.
    """

    if cash_krw < 0:
        raise ValueError("portfolio balance cash must not be negative")
    # live 잔고의 포지션 모양(symbol/quantity/marketValueKrw)이 곧 관측 스키마의 모양이다.
    # 평가액을 다시 곱하지 않고 브로커가 준 값을 그대로 쓴다.
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
        # 관측 스키마는 포지션마다 `isGoldEtfEtn` 을 요구한다(kis_mock_portfolio_writer._position).
        # 브로커 응답에는 그 구분이 없으므로 커밋된 유니버스 카탈로그로 채운다. 그대로 흘려보내면
        # 보유가 하나라도 있는 순간 적재가 ValueError 로 닫힌다 - 계좌가 비어 있는 동안에는
        # 드러나지 않다가 첫 보유에서 터진다. 실측으로 그렇게 닫혔다.
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
