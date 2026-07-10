from __future__ import annotations

import json
from datetime import date, timedelta
from importlib.resources import files
from typing import Any, Protocol

from app.data.kis.parsers import (
    CurrentPrice,
    DailyBar,
    HolidayRow,
    parse_current_price,
    parse_daily_bars,
    parse_holidays,
)
from app.data.kis.http_client import CURRENT_PRICE_PATH, DAILY_ITEMCHART_PATH, HOLIDAY_PATH
from app.data.kis.settings import KISSettings
from app.data.kis.symbols import normalize_symbol


class HttpClientLike(Protocol):
    def request(
        self,
        method: str,
        path: str,
        tr_id: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...

    def close(self) -> None: ...


class KISMarketClient:
    def __init__(
        self,
        settings: KISSettings,
        http_client: HttpClientLike,
        page_size: int = 100,
    ) -> None:
        self.settings = settings
        self.http_client = http_client
        self.page_size = page_size

    def close(self) -> None:
        """market, token, Redis runtime 자원을 성공·실패 경로 모두에서 닫는다."""
        self.http_client.close()

    def __enter__(self) -> "KISMarketClient":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def current_price(self, symbol: str) -> CurrentPrice:
        symbol = normalize_symbol(symbol)
        if self.settings.offline:
            # offline mode도 실제 CLI와 같은 runtime package fixture를 읽어 테스트 전용 경로와 어긋나지 않게 한다.
            return parse_current_price(_load_fixture(f"current_price_{symbol}.json"), symbol=symbol)
        response = self.http_client.request(
            "GET",
            CURRENT_PRICE_PATH,
            tr_id=self.settings.current_price_tr_id,
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
        )
        return parse_current_price(response, symbol=symbol)

    def daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]:
        symbol = normalize_symbol(symbol)
        if self.settings.offline:
            return self._offline_daily_bars(symbol, start, end)
        cursor_end = end
        collected: list[DailyBar] = []
        while cursor_end >= start:
            # KIS 일봉 조회는 한 번에 약 100건만 안정적으로 받는다는 전제로, 가장 오래된 날짜 직전으로
            # cursor를 이동한다. 날짜 범위가 겹쳐도 storage upsert가 symbol+date로 멱등성을 보장한다.
            response = self.http_client.request(
                "GET",
                DAILY_ITEMCHART_PATH,
                tr_id=self.settings.daily_itemchart_tr_id,
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": symbol,
                    "FID_INPUT_DATE_1": _format_date(start),
                    "FID_INPUT_DATE_2": _format_date(cursor_end),
                    "FID_PERIOD_DIV_CODE": "D",
                    "FID_ORG_ADJ_PRC": "0",
                },
            )
            page = [bar for bar in parse_daily_bars(response, symbol=symbol) if start <= bar.date <= cursor_end]
            if not page:
                break
            collected.extend(page)
            oldest = min(bar.date for bar in page)
            if len(page) < self.page_size or oldest <= start:
                break
            cursor_end = oldest - timedelta(days=1)
        return collected

    def holidays(self, base_date: date) -> list[HolidayRow]:
        if self.settings.offline:
            return parse_holidays(_load_fixture(f"holiday_{base_date:%Y%m}.json"))
        if self.settings.mode != "live":
            # chk-holiday는 모의투자 미지원 supporting read라 mock에서는 네트워크 호출 대신 명시적으로 skip한다.
            return []
        response = self.http_client.request(
            "GET",
            HOLIDAY_PATH,
            tr_id=self.settings.holiday_tr_id,
            params={"BASS_DT": _format_date(base_date), "CTX_AREA_NK": "", "CTX_AREA_FK": ""},
        )
        return parse_holidays(response)

    def _offline_daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]:
        bars: list[DailyBar] = []
        page = 1
        while True:
            fixture_name = f"daily_itemchart_{symbol}_page{page}.json"
            try:
                response = _load_fixture(fixture_name)
            except FileNotFoundError:
                break
            bars.extend(bar for bar in parse_daily_bars(response, symbol=symbol) if start <= bar.date <= end)
            page += 1
        if not bars:
            # fixture 누락은 조용히 빈 parquet을 만들면 smoke 신뢰도를 해치므로 명시 실패로 드러낸다.
            raise FileNotFoundError(f"No offline daily fixture found for {symbol}")
        return bars

def _load_fixture(name: str) -> dict[str, Any]:
    content = files("app.data.kis.fixtures").joinpath(name).read_text(encoding="utf-8")
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError(f"Fixture {name} is not a JSON object")
    return data


def _format_date(day: date) -> str:
    return day.strftime("%Y%m%d")
