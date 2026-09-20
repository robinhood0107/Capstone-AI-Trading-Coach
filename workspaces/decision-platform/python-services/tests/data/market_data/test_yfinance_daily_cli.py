"""일봉 갱신 CLI 회귀.

무엇을 지키려는 테스트인가. 이 CLI 는 자동운용이 매일 같은 낡은 시장데이터로 도는 문제를
닫으려고 만들었다. 그래서 지켜야 하는 성질이 셋이다.

  1. 세션 선정이 장중 일봉을 넣지 않는다 - 오늘은 달력이 준 마감 시각 뒤 여유가 지났을
     때만 들어온다. 오늘을 아예 제외하면 세션 D 의 종가가 D+1 에만 적재되고, 한동안 쓰지
     않다가 다시 켰을 때 빈 구간을 따라잡지 못한다.
  2. 계약을 만족하지 않는 봉을 조용히 고치지 않는다 - 버리고, 그 세션은 exact-31 이 모이지
     않아 자연히 멈춘다. 값을 억지로 맞추는 것이 가장 나쁜 결과다.
  3. 종목은 커밋된 exact-31 에서 나온다 - 티커 변환 규칙을 여기서 새로 만들지 않는다.

DB 와 네트워크를 쓰지 않고 확인할 수 있는 것만 본다. 적재 자체는 기존 production writer
(`repository.stage_daily_shard`)가 하고 그쪽에 이미 테스트가 있다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.data.calendar.models import SourceProvenance, XKRXSession
from app.data.market_data import yfinance_daily_cli as cli
from app.data.market_data.yfinance_daily_cli import (
    DailyRefreshError,
    _marker,
    _max_sessions,
    _parse,
    _pending_sessions,
    _settled,
    _universe,
)

_KST = ZoneInfo("Asia/Seoul")


def test_universe_is_the_committed_exact_31() -> None:
    universe = _universe()

    assert len(universe) == 31
    # 티커 규칙을 다시 만들지 않았음을 고정한다. 전원 KOSPI 이므로 접미사는 하나다.
    assert all(ticker == f"{symbol}.KS" for symbol, ticker in universe.items())


def test_pending_sessions_never_includes_a_future_session() -> None:
    """미래 세션은 어떤 경우에도 넣지 않는다."""

    head = date.today() - timedelta(days=30)

    pending = _pending_sessions(head, 60)

    assert all(session <= date.today() for session in pending)
    assert pending == sorted(pending)
    assert all(session > head for session in pending)


def test_pending_sessions_excludes_today_until_the_close_settles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """오늘은 마감 후 여유가 지났을 때만 넣는다.

    지키려는 것은 "오늘을 절대 넣지 않는다"가 아니라 "장중 일봉을 넣지 않는다"다. 오늘을
    아예 제외하면 세션 D 의 종가가 D+1 에만 적재되고, 한동안 쓰지 않다가 다시 켰을 때 빈
    구간을 따라잡지 못한다. 마감 시각은 달력이 준 `close_at` 을 쓰므로 반장일에도 맞는다.
    """

    session = date(2026, 9, 7)  # 개장일
    head = date(2026, 9, 4)

    def at(moment: datetime) -> list[date]:
        class Clock:
            @staticmethod
            def now(tz: object | None = None) -> datetime:
                del tz
                return moment

        monkeypatch.setattr(cli, "datetime", Clock)
        return _pending_sessions(head, 10)

    intraday = datetime(2026, 9, 7, 11, 0, tzinfo=_KST)
    just_closed = datetime(2026, 9, 7, 15, 31, tzinfo=_KST)
    settled = datetime(2026, 9, 7, 23, 0, tzinfo=_KST)

    assert session not in at(intraday)
    assert session not in at(just_closed)
    assert session in at(settled)


def test_a_session_without_a_calendar_close_is_never_settled() -> None:
    """달력이 마감 시각을 주지 못하면 확정으로 보지 않는다.

    미확정 봉을 적재하는 쪽이 늦게 적재하는 쪽보다 나쁘다.
    """

    dateless = XKRXSession(
        session_date=date(2026, 9, 7),
        is_open=True,
        open_at=None,
        close_at=None,
        timezone="Asia/Seoul",
        provenance=SourceProvenance(
            library_name="exchange-calendars",
            library_version="4.13.2",
            calendar_name="XKRX",
        ),
    )

    assert not _settled(dateless, datetime(2026, 9, 7, 23, 0, tzinfo=_KST))


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
        # 실측한 실패 모드다. 2026-09-07 마감 8시간 45분 뒤에도 Yahoo 가 31종목 전부에 대해
        # open/high/low/volume 은 주고 close 와 adjclose 만 null 로 줬다. 제공자가 일봉의
        # 종가 확정을 늦게 채우기 때문이다. 이때 시가나 고가를 종가로 대신 쓰면 그 세션의
        # 수익률이 전부 거짓이 되므로 버리고 다음 시간의 루프에 맡긴다.
        {"open": [1], "high": [1], "low": [1], "close": [None], "volume": [1]},
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


def test_marker_keeps_the_cause_and_never_breaks_the_status_line() -> None:
    """표식은 원인을 특정할 만큼 남기되 공백을 넣지 않는다.

    이 값은 `observations={...}` 로 공백 구분 key=value 줄에 들어간다. 공백이 섞이면 줄을
    읽는 쪽이 다음 키를 잃는다. 타입 이름만 남기던 탓에 실제 원인
    (`DECISION_SOURCE_WRITER_OFFLINE_TARGET` 누락)을 컨테이너 밖에서 알 수 없었다.
    """

    marker = _marker(
        ValueError("DECISION_SOURCE_WRITER_OFFLINE_TARGET must be one of local/offline/test")
    )

    assert " " not in marker
    assert "DECISION_SOURCE_WRITER_OFFLINE_TARGET" in marker
    # 값에 DSN 이 섞일 수 있어 길이를 묶는다.
    assert len(_marker(ValueError("x" * 500))) <= 120
    assert _marker(ValueError("")) == "NO_MESSAGE"
