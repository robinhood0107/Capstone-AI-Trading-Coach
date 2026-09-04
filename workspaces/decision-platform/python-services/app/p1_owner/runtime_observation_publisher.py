"""자동운용 런타임이 받은 잔고를 잔고·위험지표 관측으로 적재한다.

## 왜 런타임이 이것을 쓰는가

RiskEngine 은 관측 표를 읽는데 그 표를 채우는 것은 운영자 CLI 뿐이었고 어디에도 배선돼 있지
않았다. 그래서 자동운용이 RISK_CHECKING 에서 `violations` 는 비어 있는데 입력 부재로 HOLD 됐다.

네 축 중 잔고와 위험지표는 **KIS 잔고가 유일한 출처**이고 그 잔고를 받는 프로세스는 자동운용
런타임뿐이다. 시세와 종목카탈로그는 가격이 출처이므로 일일 수집기가 쓴다
(`app.data.decision.daily_observation_cli`). 페이로드 정의는
`app.data.decision.observation_payloads` 에서 공유한다.

## 왜 ORDER_SIZING 안인가

런타임이 그 지점에서 이미 잔고를 받고 다음 tick 이 RISK_CHECKING 이다. 같은 tick 안에서 쓰고
읽으므로 순서가 보장되고, 관측이 주문 직전의 실제 잔고를 그대로 반영한다. 세션 밖에서 미리
쓰면 그 사이의 체결을 놓친다.

## 실패는 삼키고 관측만 남긴다

적재가 실패하면 RiskEngine 이 입력 부재로 HOLD 한다 - fail-closed 이고 안전한 결과다. 여기서
예외를 올리면 세션이 HALTED 로 닫히고 사람이 손대야 다시 열린다. 잘못된 주문을 막는 것이
목적이므로 더 조용한 실패를 고른다. 대신 사유 마커를 남겨 원인이 보이게 한다.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from app.brokerage.kis_mock_portfolio_writer import append_kis_mock_portfolio_fixture
from app.data.kis.market_quote_observation_writer import append_market_quote_fixture
from app.decision_source_cli import attest_source_writer_dsn
from app.data.decision.deterministic_observation_writer import (
    append_deterministic_metric_fixture,
)
from app.data.decision.observation_payloads import (
    GOLD_ETF_SYMBOLS,
    deterministic_metrics_payload,
    market_quote_payload,
    owner_scope_hash,
    portfolio_balance_payload,
)

_SOURCE_VERSION: Final = "p1-runtime-observation-v1"
# 잔고 관측만은 소비자가 요구하는 버전을 그대로 써야 한다. p1_automation_risk_balance_
# projection_v2 가 source_version 이 'kis-mock-online-complete-v2' 인 행만 읽는다. 다른 값으로
# 적재하면 행은 남지만 자동운용은 그것을 보지 못하고 낡은 기대 projection 을 계속 쓴다 -
# 실측으로 그 때문에 ORDER_SIZING 이 매번 ACCOUNT_DRIFT 로 HALTED 됐다. 계좌에 보유가
# 생기기 전에는 낡은 값과 실제가 우연히 같아 드러나지 않았다.
_BALANCE_SOURCE_VERSION: Final = "kis-mock-online-complete-v2"
_PORTFOLIO_SOURCE: Final = "KIS_MOCK"
_PORTFOLIO_DSN_KEY: Final = "DECISION_PORTFOLIO_WRITER_DATABASE_DSN"
_RISK_DSN_KEY: Final = "DECISION_RISK_WRITER_DATABASE_DSN"
_MARKET_DSN_KEY: Final = "DECISION_MARKET_WRITER_DATABASE_DSN"
# 일일 수집기가 쓰는 것과 같은 버전이어야 소비자가 두 출처를 같은 계열로 읽는다.
_QUOTE_SOURCE_VERSION: Final = "p1-daily-quote-observation-v1"


def _write(
    payload: dict[str, Any],
    writer: Any,
    dsn: str,
    *,
    expected_role: str,
    allowed_insert_tables: tuple[str, ...],
) -> int:
    """운영자 CLI 와 같은 사전 검증을 통과한 DSN 으로만 적재한다.

    DSN 이 컨테이너에 있다는 것만으로 쓰지 않는다. `attest_source_writer_dsn` 이 current_user 가
    기대한 좁은 role 인지, 그 role 이 자기 표 두 개에만 INSERT 를 갖는지, 다른 표를 바꿀 수
    없는지를 실제 권한으로 확인한다. 즉 자동운용이 실수로 넓은 DSN 을 물려받아도 거부된다.
    """

    attest_source_writer_dsn(
        dsn, expected_role=expected_role, allowed_insert_tables=allowed_insert_tables
    )
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        path = Path(handle.name)
    try:
        return int(writer(path, database_dsn=dsn))
    finally:
        path.unlink(missing_ok=True)


def publish_runtime_observations(
    *,
    owner_user_id: str,
    account_id: str,
    balance: Mapping[str, Any],
    baseline_equity_krw: int,
    trading_date: str,
    quotes: Mapping[str, int] | None = None,
) -> str:
    """잔고·위험지표 관측을 적재하고 결과 마커를 돌려준다. 예외를 올리지 않는다.

    `baseline_equity_krw` 는 durable state 의 기준 자본이고 위험지표의 분모다. 현재 자본이
    기준보다 낮은 만큼이 그날의 손실률이자 낙폭이다 - 지어내지 않는다.

    당일 주문 수는 이 시점에 0 이다. 런타임은 세션당 최대 한 건을 내고 이 호출은 그 주문
    **직전**이며, `no_open_order` 게이트가 미체결 주문이 남은 세션의 진입을 이미 막는다.
    """

    portfolio_dsn = os.environ.get(_PORTFOLIO_DSN_KEY, "").strip()
    risk_dsn = os.environ.get(_RISK_DSN_KEY, "").strip()
    if not portfolio_dsn or not risk_dsn:
        return "SKIPPED_DSN_MISSING"

    raw_positions = balance.get("positions")
    positions = (
        [item for item in raw_positions if isinstance(item, dict)]
        if isinstance(raw_positions, list)
        else []
    )
    now = datetime.now(UTC)
    try:
        scope_hash = owner_scope_hash(account_id)
        cash_krw = int(balance["cashKrw"])
        portfolio = portfolio_balance_payload(
            owner_user_id=owner_user_id,
            scope_hash=scope_hash,
            cash_krw=cash_krw,
            positions=positions,
            now=now,
            source_version=_BALANCE_SOURCE_VERSION,
            gold_etf_symbols=GOLD_ETF_SYMBOLS,
        )
        metrics = deterministic_metrics_payload(
            owner_user_id=owner_user_id,
            scope_hash=scope_hash,
            portfolio_source=_PORTFOLIO_SOURCE,
            equity_krw=int(portfolio["portfolioEquityKrw"]),
            baseline_equity_krw=baseline_equity_krw,
            daily_order_count=0,
            trading_date=trading_date,
            now=now,
            source_version=_SOURCE_VERSION,
        )
        # 시세 관측의 신선도 창은 관측 +5분이다. 일일 수집기가 하루 한 번 넣는 값으로는 장중
        # 판정을 만족할 수 없어 RiskEngine 이 PRICE_MISSING/PRICE_STALE 로 HOLD 한다 -
        # 실측으로 violations 는 0 건이고 issues 만 넷이었다. 주문 직전 실시간 호가를 가진
        # 프로세스는 이 런타임뿐이므로 여기서 같은 tick 안에 적재한다. DSN 이 없으면 건너뛴다 -
        # 관측이 없으면 판정이 HOLD 로 닫히므로 결과는 이미 fail-closed 다.
        market_dsn = os.environ.get(_MARKET_DSN_KEY, "").strip()
        if quotes and market_dsn:
            _write(
                market_quote_payload(dict(quotes), now=now, source_version=_QUOTE_SOURCE_VERSION),
                append_market_quote_fixture,
                market_dsn,
                expected_role="decision_market_writer",
                allowed_insert_tables=(
                    "market_quote_observations",
                    "instrument_catalog_observations",
                ),
            )
        _write(
            portfolio,
            append_kis_mock_portfolio_fixture,
            portfolio_dsn,
            expected_role="decision_portfolio_writer",
            allowed_insert_tables=(
                "portfolio_balance_observations",
                "portfolio_position_observations",
            ),
        )
        _write(
            metrics,
            append_deterministic_metric_fixture,
            risk_dsn,
            expected_role="decision_risk_writer",
            allowed_insert_tables=(
                "deterministic_risk_observations",
                "daily_order_count_observations",
            ),
        )
    except (KeyError, TypeError, ValueError, OSError) as error:
        # 값이나 파일 문제. 사유만 남기고 HOLD 로 닫히게 둔다.
        return f"FAILED_{type(error).__name__}"
    except Exception as error:  # noqa: BLE001 - psycopg 오류를 여기서 삼킨다
        # DB 오류로 세션을 HALTED 로 만들지 않는다. 관측이 없으면 RiskEngine 이 HOLD 한다.
        return f"FAILED_{type(error).__name__}"
    return "PUBLISHED"
