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
from app.data.kis.settings import KISSettings

CURRENT_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
DAILY_ITEMCHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
HOLIDAY_PATH = "/uapi/domestic-stock/v1/quotations/chk-holiday"


class HttpClientLike(Protocol):
    def request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class TokenManagerLike(Protocol):
    def get_access_token(self) -> str: ...


class KISMarketClient:
    def __init__(
        self,
        settings: KISSettings,
        http_client: HttpClientLike,
        token_manager: TokenManagerLike,
        page_size: int = 100,
    ) -> None:
        self.settings = settings
        self.http_client = http_client
        self.token_manager = token_manager
        self.page_size = page_size

    def current_price(self, symbol: str) -> CurrentPrice:
        if self.settings.offline:
            return parse_current_price(_load_fixture(f"current_price_{symbol}.json"), symbol=symbol)
        response = self.http_client.request(
            "GET",
            CURRENT_PRICE_PATH,
            headers=self._headers(self.settings.current_price_tr_id),
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
        )
        return parse_current_price(response, symbol=symbol)

    def daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]:
        if self.settings.offline:
            return self._offline_daily_bars(symbol, start, end)
        cursor_end = end
        collected: list[DailyBar] = []
        while cursor_end >= start:
            response = self.http_client.request(
                "GET",
                DAILY_ITEMCHART_PATH,
                headers=self._headers(self.settings.daily_itemchart_tr_id),
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
            return []
        response = self.http_client.request(
            "GET",
            HOLIDAY_PATH,
            headers=self._headers(self.settings.holiday_tr_id),
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
            raise FileNotFoundError(f"No offline daily fixture found for {symbol}")
        return bars

    def _headers(self, tr_id: str) -> dict[str, str]:
        # appkey/appsecret은 KIS 필수 헤더지만 로그·리포트에는 절대 쓰지 않는다.
        return {
            "authorization": f"Bearer {self.token_manager.get_access_token()}",
            "appkey": self.settings.app_key or "",
            "appsecret": self.settings.app_secret or "",
            "tr_id": tr_id,
            "custtype": "P",
        }


def _load_fixture(name: str) -> dict[str, Any]:
    content = files("app.data.kis.fixtures").joinpath(name).read_text(encoding="utf-8")
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError(f"Fixture {name} is not a JSON object")
    return data


def _format_date(day: date) -> str:
    return day.strftime("%Y%m%d")
