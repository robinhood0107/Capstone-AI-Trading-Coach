"""공개 일봉 피드로 빠진 거래일의 DAILY shard 를 채운다.

왜 필요한가. 자동운용 루프는 매일 돌지만 시장데이터를 스스로 갱신하지 않았다.
`market_data_bars` 를 쓰는 production 경로는 `repository.adopt_daily_shard` 하나이고 그것을
부르는 것은 운영자 명령뿐이었다. 그래서 일일 추론이 항상 성립하기는 하되(소스 세션과 이력의
마지막 바가 같은 manifest 상태에서 파생되어 함께 밀린다) 매일 같은 창으로 같은 신호를 냈다.
가용성 문제가 아니라 최신성 문제였고 그것을 걸러내는 게이트가 없었다.

무엇을 새로 만들지 않았는가. 이 파일은 배관만 잇는다.

  적재      repository.stage_daily_shard -> adopt_daily_shard (기존 production writer).
            manifest 체인 확인·writer role 강제·한 트랜잭션 삽입을 그대로 쓴다.
  종목      contracts/catalogs/p1-return-universe.v1.json 의 exact-31 과 yfinanceTicker.
            티커 변환을 다시 만들지 않는다. 이미지에 이 파일이 함께 들어 있다.
  세션      오프라인 XKRX 달력(순수 exchange_calendars). trading_sessions 를 채운 것과
            같은 출처라 결과가 구성상 일치한다.
  기준점    current_market_data_manifest_head - SECURITY DEFINER 이고 writer 에게 EXECUTE
            가 있다. production writer 가 체인 확인에 쓰는 그 함수다.
  실행      기존 market-data-cli compose 서비스. 새 서비스도 새 secret 도 만들지 않는다.
  HTTP      이미 있는 httpx. yfinance 패키지를 production 이미지에 넣지 않는다 -
            일봉 하나 받으려고 curl_cffi/beautifulsoup4/peewee 를 끌고 들어올 이유가 없다.

정직성. 출처는 공개 지연 피드이고 종가는 액면분할이 반영된 값이다. 그래서 bar 의
temporalQuality 를 COLLECTION_ONLY 로 남긴다 - 공식 시세 vintage 라고 주장하지 않는다.
`asOf` 는 해당 세션의 장 마감(15:30 KST)으로 둔다. 받은 시각이 아니라 데이터가 가리키는
시각이 그것이고, 다음 세션의 09:20 상한도 자연히 만족한다.

SELECT 는 한 건도 하지 않는다. writer role 은 INSERT 전용이고 market_data_bars 는 어떤
role 도 SELECT 하지 못한다 - 역할 분리라는 안전 경계이므로 권한을 넓히지 않았다.

한 세션은 exact-31 이 모두 모일 때만 적재한다. 하나라도 비면 그 세션에서 멈춘다 - 부분
적재는 하류가 31종목을 가정하는 계약을 깨고, 체인은 연속이어야 한다.

필수 환경변수:
  MARKET_DATA_WRITER_DSN            decision_market_writer role DSN

선택 환경변수:
  P1_MARKET_DATA_YF_MAX_SESSIONS    한 번에 채울 최대 세션 수 (기본 10, 상한 60)
  P1_MARKET_DATA_YF_BASE_URL        일봉 조회 base URL (기본 Yahoo chart v8)
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final, cast
from zoneinfo import ZoneInfo

import httpx
import psycopg

from app.data._shared.canonical_json import canonical_json_sha256
from app.data.calendar.adapters.xkrx import (
    build_xkrx_sessions_in_range,
    xkrx_calendar_bounds,
)
from app.data.market_data.daily_runtime import AcceptedDailyShard
from app.data.decision.observation_payloads import (
    GOLD_ETF_SYMBOLS,
    instrument_catalog_payload,
    market_quote_payload,
)
from app.data.kis.instrument_catalog_writer import append_instrument_catalog_fixture
from app.data.kis.market_quote_observation_writer import append_market_quote_fixture
from app.data.market_data.repository import MarketDataRepositoryError, stage_daily_shard
from app.decision_source_cli import attest_source_writer_dsn

_KST = ZoneInfo("Asia/Seoul")
_CLOSE = time(15, 30)
_DEFAULT_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
_CALENDAR_REVISION = "xkrx-4.13.2"
_SOURCE = "public-daily-ohlcv-v1"
_MAX_SESSIONS_CAP = 60
# 카탈로그의 tickerSuffixRule 을 그대로 옮긴 것이다. 새 규칙을 만들지 않는다.
_TICKER_SUFFIX = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}
_TIMEOUT_SECONDS = 20.0


# 커밋된 유니버스 카탈로그가 정하는 사실이다. 여기서 규칙을 만들지 않는다.
_OBSERVATION_SOURCE_VERSION: Final = "p1-daily-observation-v1"


class DailyRefreshError(RuntimeError):
    """이 CLI 가 스스로 판정한 실패. 사유를 마커로 남기고 종료한다."""


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    session_date: date
    open_price: int
    high_price: int
    low_price: int
    close_price: int
    volume: int


def main() -> int:
    """빠진 거래일을 순서대로 채운다. 체인이 끊기지 않게 한 세션씩 적재한다."""

    dsn = os.environ.get("MARKET_DATA_WRITER_DSN", "").strip()
    if not dsn:
        print("P1_MARKET_DATA_YF_REFRESH=INVALID reason=DSN_MISSING providerCalls=0")
        return 2
    try:
        max_sessions = _max_sessions()
        base_url = os.environ.get("P1_MARKET_DATA_YF_BASE_URL", _DEFAULT_BASE_URL).strip()
    except DailyRefreshError as error:
        print(f"P1_MARKET_DATA_YF_REFRESH=INVALID reason={error} providerCalls=0")
        return 2

    try:
        universe = _universe()
        with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as connection:
            _, head_session = _current_head(connection)
        pending = _pending_sessions(head_session, max_sessions)
    except DailyRefreshError as error:
        print(f"P1_MARKET_DATA_YF_REFRESH=INVALID reason={error} providerCalls=0")
        return 2
    except psycopg.Error as error:
        print(
            "P1_MARKET_DATA_YF_REFRESH=DATABASE_UNAVAILABLE "
            f"reason=HEAD_LOOKUP_FAILED_{type(error).__name__} providerCalls=0"
        )
        return 2

    if not pending:
        print("P1_MARKET_DATA_YF_REFRESH=UP_TO_DATE sessions=0 providerCalls=0")
        return 0

    calls = 0
    try:
        # head 세션까지 함께 받는다. 적재 루프는 pending 만 돌므로 다시 적재되지 않고,
        # provider 가 신규 세션의 종가를 아직 채우지 않아도 직전 완전한 세션의 종가로 관측을
        # 남길 수 있다.
        fetch_from = head_session or pending[0]
        frames, calls = _fetch(base_url, universe, fetch_from, pending[-1])
    except DailyRefreshError as error:
        print(
            f"P1_MARKET_DATA_YF_REFRESH=PROVIDER_UNAVAILABLE reason={error} providerCalls={calls}"
        )
        return 1

    adopted: list[str] = []
    stopped: str | None = None
    published = "NOT_ATTEMPTED"
    for session in pending:
        bars = [frames[symbol][session] for symbol in universe if session in frames[symbol]]
        if len(bars) != len(universe):
            # 부분 적재는 하지 않는다. 하류가 exact-31 을 가정하고 체인은 연속이어야 한다.
            stopped = f"{session.isoformat()}:INCOMPLETE_{len(bars)}_OF_{len(universe)}"
            break
        try:
            outcome = _adopt(dsn, session, bars)
        except (MarketDataRepositoryError, psycopg.Error) as error:
            stopped = f"{session.isoformat()}:{type(error).__name__}"
            break
        adopted.append(f"{session.isoformat()}:{outcome}")

    complete = _newest_complete_session(frames, universe)
    if complete is None:
        published = "SKIPPED_NO_COMPLETE_SESSION"
    else:
        published = _publish_observations(dsn, [frames[symbol][complete] for symbol in universe])

    print(
        "P1_MARKET_DATA_YF_REFRESH="
        + ("STOPPED" if stopped and not adopted else "ADOPTED" if adopted else "STOPPED")
        + f" sessions={len(adopted)}"
        + f" adopted={','.join(adopted) if adopted else 'none'}"
        + f" stoppedAt={stopped or 'none'}"
        + f" symbols={len(universe)}"
        + f" temporalQuality=COLLECTION_ONLY observations={published}"
        + f" providerCalls={calls}"
    )
    return 0 if adopted and not stopped else (1 if stopped else 0)


def _newest_complete_session(
    frames: dict[str, dict[date, Bar]], universe: Iterable[str]
) -> date | None:
    """exact-31 을 모두 덮는 가장 최근 세션. 없으면 None.

    부분 커버리지는 쓰지 않는다 - 하류가 exact-31 을 가정하고, 일부 종목만 있는 시세 관측은
    나머지 종목 판정을 PRICE_MISSING 으로 닫는다.
    """

    sessions: set[date] = set()
    for symbol in universe:
        sessions |= set(frames.get(symbol, {}))
    for session in sorted(sessions, reverse=True):
        if all(session in frames.get(symbol, {}) for symbol in universe):
            return session
    return None


def _publish_observations(dsn: str, bars: list[Bar]) -> str:
    """방금 적재한 세션의 종가를 시세·종목카탈로그 관측으로도 남긴다.

    RiskEngine 은 주문을 판정할 때 관측 표에서 가격과 ETF 여부를 읽는데, 그 표를 채우는 것은
    운영자 CLI 뿐이었고 어디에도 배선돼 있지 않았다. 그래서 자동운용이 `violations` 는 비어
    있는데 `PRICE_MISSING` / `BROKERAGE_UNAVAILABLE` 로 HOLD 됐다. 값의 출처가 이 수집기이므로
    여기서 함께 남기는 것이 옳다 - 표를 다시 읽지 않으므로 SELECT 권한도 새 비밀도 필요 없고,
    같은 `MARKET_DATA_WRITER_DSN` 을 그대로 쓴다.

    적재 시점에 한 번이면 충분하다. 신선도 규칙은 `observedAt >= 직전 개장일 장 마감` 이고 이
    호출은 그 세션이 마감된 뒤에 일어나므로 다음 거래일 판정이 이 관측을 FRESH 로 본다. 이미
    최신이면(`UP_TO_DATE`) 그날 몫은 그 세션을 적재할 때 이미 남겼다.

    실패는 마커로만 남긴다. 관측이 없으면 RiskEngine 이 HOLD 하므로 결과가 이미 fail-closed 이고,
    시장데이터 적재 자체를 되돌릴 이유가 없다.
    """

    prices = {bar.symbol: bar.close_price for bar in bars}
    now = datetime.now(UTC)
    try:
        quotes = _append_observation(
            market_quote_payload(prices, now=now, source_version=_OBSERVATION_SOURCE_VERSION),
            append_market_quote_fixture,
            dsn,
        )
        instruments = _append_observation(
            instrument_catalog_payload(
                sorted(prices),
                GOLD_ETF_SYMBOLS,
                now=now,
                source_version=_OBSERVATION_SOURCE_VERSION,
            ),
            append_instrument_catalog_fixture,
            dsn,
        )
    except (ValueError, OSError, psycopg.Error) as error:
        return f"FAILED_{type(error).__name__}"
    return f"PUBLISHED_{quotes}_{instruments}"


def _append_observation(payload: dict[str, Any], writer: Any, dsn: str) -> int:
    """운영자 CLI 와 같은 사전 검증을 통과한 DSN 으로만 적재한다.

    DSN 이 컨테이너에 있다는 것만으로 쓰지 않는다. `attest_source_writer_dsn` 이 current_user 가
    `decision_market_writer` 인지, 그 role 이 자기 표 두 개에만 INSERT 를 갖는지, 다른 표를
    바꿀 수 없는지를 실제 권한으로 확인한다.
    """

    attest_source_writer_dsn(
        dsn,
        expected_role="decision_market_writer",
        allowed_insert_tables=("market_quote_observations", "instrument_catalog_observations"),
    )
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        path = Path(handle.name)
    try:
        return int(writer(path, database_dsn=dsn))
    finally:
        path.unlink(missing_ok=True)


def _max_sessions() -> int:
    raw = os.environ.get("P1_MARKET_DATA_YF_MAX_SESSIONS", "").strip()
    if not raw:
        return 10
    try:
        value = int(raw)
    except ValueError:
        raise DailyRefreshError("MAX_SESSIONS_INVALID") from None
    if not 1 <= value <= _MAX_SESSIONS_CAP:
        raise DailyRefreshError("MAX_SESSIONS_OUT_OF_RANGE") from None
    return value


def _catalog_path() -> Path:
    """커밋된 유니버스 카탈로그를 찾는다.

    호스트에서는 리포 루트가 다섯 단계 위이고 컨테이너에서는 /app 이 세 단계 위다. 한 상수로
    맞출 수 없으니 위로 걸어 올라가며 찾는다.
    """

    relative = Path("contracts/catalogs/p1-return-universe.v1.json")
    for parent in Path(__file__).resolve().parents:
        candidate = parent / relative
        if candidate.is_file():
            return candidate
    raise DailyRefreshError("UNIVERSE_CATALOG_MISSING")


def _universe() -> dict[str, str]:
    """커밋된 exact-31 에서 종목과 티커를 읽는다. 변환 규칙을 다시 만들지 않는다."""

    catalog = json.loads(_catalog_path().read_text(encoding="utf-8"))
    universe = {
        str(entry["symbol"]): str(entry["yfinanceTicker"])
        for entry in cast(list[dict[str, Any]], catalog["symbols"])
    }
    if len(universe) != int(catalog["symbolCount"]):
        raise DailyRefreshError("UNIVERSE_CATALOG_INCONSISTENT")
    return dict(sorted(universe.items()))


def _current_head(connection: psycopg.Connection[Any]) -> tuple[str, date]:
    """현재 ACCEPTED head 의 sha 와 세션을 얻는다.

    SECURITY DEFINER 함수라 writer role 로도 부를 수 있다. 먼 미래를 후보로 주면 조건이
    session_date < candidate 뿐이므로 곧 현재 head 다.
    """

    row = connection.execute(
        "SELECT manifest_sha256, session_date FROM current_market_data_manifest_head(%s)",
        (date(2099, 1, 1),),
    ).fetchone()
    if row is None:
        raise DailyRefreshError("MANIFEST_HEAD_MISSING")
    return str(row[0]), cast(date, row[1])


def _pending_sessions(head_session: date, limit: int) -> list[date]:
    """head 이후 오늘 전까지의 개장일을 오래된 순으로 돌려준다.

    오늘은 넣지 않는다. 장중에는 일봉이 확정되지 않는다. 달력은 오프라인 XKRX 이므로 DB 읽기가
    없고 trading_sessions 를 채운 출처와 같다.
    """

    today = datetime.now(_KST).date()
    start = head_session + timedelta(days=1)
    if start >= today:
        return []
    calendar_first, calendar_last = xkrx_calendar_bounds()
    start = max(start, calendar_first)
    end = min(today - timedelta(days=1), calendar_last)
    if end < start:
        return []
    sessions = [
        built.session_date for built in build_xkrx_sessions_in_range(start, end) if built.is_open
    ]
    return sorted(sessions)[:limit]


def _fetch(
    base_url: str, universe: dict[str, str], first: date, last: date
) -> tuple[dict[str, dict[date, Bar]], int]:
    """종목별 일봉을 받는다. 창을 넉넉히 잡아 한 종목당 호출을 한 번으로 끝낸다."""

    start = datetime.combine(first - timedelta(days=5), time(0, 0), _KST)
    end = datetime.combine(last + timedelta(days=2), time(0, 0), _KST)
    frames: dict[str, dict[date, Bar]] = {}
    calls = 0
    headers = {"accept": "application/json", "user-agent": "capstone-p1-market-data/1.0"}
    with httpx.Client(timeout=_TIMEOUT_SECONDS, headers=headers) as client:
        for symbol, ticker in universe.items():
            calls += 1
            try:
                response = client.get(
                    f"{base_url}/{ticker}",
                    params={
                        "period1": int(start.timestamp()),
                        "period2": int(end.timestamp()),
                        "interval": "1d",
                    },
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as error:
                raise DailyRefreshError(f"FETCH_FAILED_{symbol}_{type(error).__name__}") from None
            frames[symbol] = _parse(symbol, payload)
    return frames, calls


def _parse(symbol: str, payload: Any) -> dict[date, Bar]:
    chart = (payload or {}).get("chart") or {}
    results = chart.get("result") or []
    if chart.get("error") or not results:
        raise DailyRefreshError(f"FETCH_EMPTY_{symbol}")
    result = results[0]
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    bars: dict[date, Bar] = {}
    for index, stamp in enumerate(stamps):
        values = [
            quote.get("open", [None] * len(stamps))[index],
            quote.get("high", [None] * len(stamps))[index],
            quote.get("low", [None] * len(stamps))[index],
            quote.get("close", [None] * len(stamps))[index],
            quote.get("volume", [None] * len(stamps))[index],
        ]
        if any(value is None for value in values):
            # 결손 봉은 버린다. 그 세션은 exact-31 이 모이지 않아 자연히 멈춘다.
            continue
        open_price, high_price, low_price, close_price, volume = (
            round(float(values[0])),
            round(float(values[1])),
            round(float(values[2])),
            round(float(values[3])),
            int(float(values[4])),
        )
        # 표의 CHECK 를 만족하지 않는 봉은 버린다. 값을 억지로 고치지 않는다.
        if min(open_price, high_price, low_price, close_price) <= 0 or volume < 0:
            continue
        if high_price < max(open_price, close_price) or low_price > min(open_price, close_price):
            continue
        session = datetime.fromtimestamp(int(stamp), UTC).astimezone(_KST).date()
        bars[session] = Bar(
            symbol=symbol,
            session_date=session,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            close_price=close_price,
            volume=volume,
        )
    return bars


def _adopt(dsn: str, session: date, bars: list[Bar]) -> str:
    """한 세션을 기존 production writer 로 적재한다. 체인 head 는 writer 가 확인한다."""

    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as connection:
        head = connection.execute(
            "SELECT manifest_sha256 FROM current_market_data_manifest_head(%s)", (session,)
        ).fetchone()
    if head is None:
        raise MarketDataRepositoryError("market-data manifest head is missing")

    as_of = datetime.combine(session, _CLOSE, _KST)
    receipts = [
        {
            "operationId": f"{_SOURCE}:{bar.symbol}:{session.isoformat()}",
            "sessionDate": session.isoformat(),
            "source": _SOURCE,
            "symbol": bar.symbol,
        }
        for bar in bars
    ]
    receipt_sha = {receipt["symbol"]: canonical_json_sha256(receipt) for receipt in receipts}
    descriptor = {
        "asOf": as_of.isoformat(),
        "bars": [
            {
                "close": bar.close_price,
                "high": bar.high_price,
                "low": bar.low_price,
                "open": bar.open_price,
                "symbol": bar.symbol,
                "volume": bar.volume,
            }
            for bar in bars
        ],
        "sessionDate": session.isoformat(),
        "source": _SOURCE,
    }
    payload: dict[str, object] = {
        "asOf": as_of,
        "bars": [
            {
                "close": bar.close_price,
                "currency": "KRW",
                "high": bar.high_price,
                "low": bar.low_price,
                "open": bar.open_price,
                "sessionDate": bar.session_date,
                "sourceReceiptSha256": receipt_sha[bar.symbol],
                "symbol": bar.symbol,
                "temporalQuality": "COLLECTION_ONLY",
                "volume": bar.volume,
            }
            for bar in bars
        ],
        "calendar": {
            "attestationSha256": canonical_json_sha256(
                {"revision": _CALENDAR_REVISION, "sessionDate": session.isoformat()}
            ),
            "revision": _CALENDAR_REVISION,
        },
        "generation": 1,
        "indices": [],
        "macro": [],
        "manifestSha256": canonical_json_sha256(descriptor),
        "previousAcceptedManifestSha256": head[0],
        "sessionDate": session,
        "sourceReceipts": receipts,
        "supersedesSha256": None,
    }
    accepted = AcceptedDailyShard(payload=payload, universe_rows=())
    result = stage_daily_shard(
        database_dsn=dsn,
        accepted=accepted,
        expected_manifest_sha256=cast(str, payload["manifestSha256"]),
    )
    return f"{result.outcome}({result.bars})"


if __name__ == "__main__":
    raise SystemExit(main())
