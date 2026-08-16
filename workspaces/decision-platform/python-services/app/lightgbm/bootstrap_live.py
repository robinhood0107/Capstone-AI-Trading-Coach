"""기존 private credential transports를 S5.6 physical-call ports에 연결한다."""

from __future__ import annotations

from datetime import date

from app.data.ecos.http_client import ECOSHttpClient
from app.data.ecos.models import ECOSObservation
from app.data.ecos.series_registry import ECOSSeries
from app.data.kis.http_client import DAILY_ITEMCHART_PATH, KISHttpClient
from app.data.kis.parsers import DailyBar, parse_daily_bars
from app.data.krx.client import KrxOpenApiClient
from app.lightgbm.errors import DatasetUnavailable


class LiveKrxBootstrapProvider:
    """KRX client의 exact seven-service method 외 경로를 노출하지 않는다."""

    def __init__(self, client: KrxOpenApiClient) -> None:
        self._client = client

    def fetch(self, *, service: str, session_date: date) -> tuple[dict[str, str], ...]:
        return self._client.fetch_s5_production_rows(session_date, service=service)


class LiveKisBootstrapProvider:
    """KIS token 준비와 한 page GET을 분리해 executor가 물리 호출을 정확히 센다."""

    def __init__(self, client: KISHttpClient) -> None:
        self._client = client

    def prepare_access_token(self) -> None:
        self._client.prepare_access_token()

    def require_cached_token_only(self) -> None:
        self._client.freeze_access_token_refresh()

    def fetch_page(
        self, *, symbol: str, start: date, end: date
    ) -> tuple[DailyBar, ...]:
        response = self._client.request(
            "GET",
            DAILY_ITEMCHART_PATH,
            tr_id="FHKST03010100",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            },
        )
        rows = tuple(
            parse_daily_bars(response, symbol=symbol, require_adjustment_fields=True)
        )
        if len(rows) > 100:
            raise DatasetUnavailable("KIS_HISTORY_UNAVAILABLE")
        return rows


class LiveEcosBootstrapProvider:
    """366-day 이하 date chunk를 page 1..400 한 번으로만 조회한다."""

    def __init__(self, client: ECOSHttpClient) -> None:
        self._client = client

    def fetch(
        self, *, series: ECOSSeries, start: date, end: date
    ) -> tuple[ECOSObservation, ...]:
        page = self._client.statistic_search(
            series=series,
            start=start,
            end=end,
            page_start=1,
            page_end=400,
        )
        if (
            page.status != "complete"
            or page.total_count != len(page.observations) + page.duplicate_count
        ):
            raise DatasetUnavailable("DATASET_UNAVAILABLE: ECOS page is incomplete")
        return tuple(page.observations)
