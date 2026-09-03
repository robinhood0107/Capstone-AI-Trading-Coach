"""일봉 갱신 CLI 회귀.

무엇을 지키려는 테스트인가. 이 CLI 는 자동운용이 매일 같은 낡은 시장데이터로 도는 문제를
닫으려고 만들었다. 그래서 지켜야 하는 성질이 셋이다.

  1. 세션 선정이 오늘을 포함하지 않는다 - 장중 일봉은 확정되지 않았다.
  2. 계약을 만족하지 않는 봉을 조용히 고치지 않는다 - 버리고, 그 세션은 exact-31 이 모이지
     않아 자연히 멈춘다. 값을 억지로 맞추는 것이 가장 나쁜 결과다.
  3. 종목은 커밋된 exact-31 에서 나온다 - 티커 변환 규칙을 여기서 새로 만들지 않는다.

DB 와 네트워크를 쓰지 않고 확인할 수 있는 것만 본다. 적재 자체는 기존 production writer
(`repository.stage_daily_shard`)가 하고 그쪽에 이미 테스트가 있다.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.data.market_data.yfinance_daily_cli import (
    DailyRefreshError,
    _max_sessions,
    _parse,
    _pending_sessions,
    _universe,
)


def test_universe_is_the_committed_exact_31() -> None:
    universe = _universe()

    assert len(universe) == 31
    # 티커 규칙을 다시 만들지 않았음을 고정한다. 전원 KOSPI 이므로 접미사는 하나다.
    assert all(ticker == f"{symbol}.KS" for symbol, ticker in universe.items())


def test_pending_sessions_never_includes_today() -> None:
    """오늘이 개장일이어도 넣지 않는다. 장중 일봉은 확정되지 않는다."""

    head = date.today() - timedelta(days=30)

    pending = _pending_sessions(head, 60)

    assert all(session < date.today() for session in pending)
    assert pending == sorted(pending)
    assert all(session > head for session in pending)


def test_pending_sessions_is_empty_when_head_is_current() -> None:
    assert _pending_sessions(date.today(), 10) == []
    assert _pending_sessions(date.today() + timedelta(days=5), 10) == []


def test_pending_sessions_respects_the_limit() -> None:
    head = date.today() - timedelta(days=120)

    assert len(_pending_sessions(head, 3)) == 3


def _payload(**quote: list[object]) -> dict[str, object]:
    # 2026-09-01 09:00 KST
    return {
        "chart": {
            "error": None,
            "result": [{"timestamp": [1756684800], "indicators": {"quote": [quote]}}],
        }
    }


def test_parse_keeps_a_well_formed_bar() -> None:
    bars = _parse(
        "005930",
        _payload(open=[249000], high=[260000], low=[246000], close=[260000], volume=[18270969]),
    )

    (bar,) = bars.values()
    assert (bar.open_price, bar.high_price, bar.low_price, bar.close_price) == (
        249000,
        260000,
        246000,
        260000,
    )
    assert bar.volume == 18270969


@pytest.mark.parametrize(
    "quote",
    [
        # 결손 - 값을 만들어 채우지 않는다.
        {"open": [None], "high": [1], "low": [1], "close": [1], "volume": [1]},
        {"open": [1], "high": [1], "low": [1], "close": [1], "volume": [None]},
        # 0 이하 가격 - 표의 CHECK 를 만족하지 않는다.
        {"open": [0], "high": [1], "low": [1], "close": [1], "volume": [1]},
        # high 가 시가·종가보다 낮다.
        {"open": [100], "high": [90], "low": [80], "close": [95], "volume": [1]},
        # low 가 시가·종가보다 높다.
        {"open": [100], "high": [120], "low": [110], "close": [105], "volume": [1]},
    ],
)
def test_parse_drops_bars_that_violate_the_contract(quote: dict[str, list[object]]) -> None:
    """고치지 않고 버린다. 그 세션은 exact-31 이 모이지 않아 적재가 멈춘다."""

    assert _parse("005930", _payload(**quote)) == {}


def test_parse_rejects_an_empty_provider_response() -> None:
    with pytest.raises(DailyRefreshError, match="FETCH_EMPTY"):
        _parse("005930", {"chart": {"error": None, "result": []}})


def test_max_sessions_refuses_values_outside_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("P1_MARKET_DATA_YF_MAX_SESSIONS", "0")
    with pytest.raises(DailyRefreshError, match="OUT_OF_RANGE"):
        _max_sessions()

    monkeypatch.setenv("P1_MARKET_DATA_YF_MAX_SESSIONS", "61")
    with pytest.raises(DailyRefreshError, match="OUT_OF_RANGE"):
        _max_sessions()

    monkeypatch.setenv("P1_MARKET_DATA_YF_MAX_SESSIONS", "not-a-number")
    with pytest.raises(DailyRefreshError, match="INVALID"):
        _max_sessions()

    monkeypatch.delenv("P1_MARKET_DATA_YF_MAX_SESSIONS")
    assert _max_sessions() == 10
