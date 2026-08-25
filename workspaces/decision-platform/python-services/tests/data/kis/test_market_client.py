from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from app.data.kis.market_client import KISMarketClient
from app.data.kis.settings import KISSettings


class _NoNetwork:
    def request(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("offline mode must not perform network calls")


class _FakeHttp:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.params_seen: list[dict[str, str]] = []

    def request(self, method: str, path: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        assert method == "GET"
        assert path.endswith("/inquire-daily-itemchartprice")
        assert tr_id == "FHKST03010100"
        self.params_seen.append(params)
        return self.responses.pop(0)


def test_offline_mode_loads_fixtures_without_network(tmp_path: Path) -> None:
    settings = KISSettings(kis_offline=True, kis_data_dir=tmp_path)
    client = KISMarketClient(settings, http_client=_NoNetwork())

    assert client.current_price("005930").price == 73500
    assert client.current_price("000660").price == 240000
    assert client.current_price("005380").symbol == "005380"
    assert client.daily_bars("005930", date(2026, 7, 7), date(2026, 7, 8))[0].symbol == "005930"
    assert client.daily_bars("000660", date(2026, 7, 8), date(2026, 7, 8))[0].symbol == "000660"
    assert client.daily_bars("005380", date(2026, 7, 8), date(2026, 7, 8))[0].symbol == "005380"


def test_daily_backfill_moves_end_date_to_oldest_seen_minus_one(tmp_path: Path) -> None:
    settings = KISSettings(kis_mode="mock", kis_data_dir=tmp_path, _env_file=None)
    page1 = {
        "rt_cd": "0",
        "output2": [
            {
                "stck_bsop_date": "20260708",
                "stck_oprc": "1",
                "stck_hgpr": "1",
                "stck_lwpr": "1",
                "stck_clpr": "1",
                "acml_vol": "1",
            },
            {
                "stck_bsop_date": "20260707",
                "stck_oprc": "1",
                "stck_hgpr": "1",
                "stck_lwpr": "1",
                "stck_clpr": "1",
                "acml_vol": "1",
            },
        ],
    }
    page2 = {
        "rt_cd": "0",
        "output2": [
            {
                "stck_bsop_date": "20260706",
                "stck_oprc": "1",
                "stck_hgpr": "1",
                "stck_lwpr": "1",
                "stck_clpr": "1",
                "acml_vol": "1",
            }
        ],
    }
    fake_http = _FakeHttp([page1, page2])
    client = KISMarketClient(settings, http_client=fake_http, page_size=2)

    bars = client.daily_bars("005930", date(2026, 7, 6), date(2026, 7, 8))

    assert [bar.date for bar in bars] == [date(2026, 7, 8), date(2026, 7, 7), date(2026, 7, 6)]
    assert fake_http.params_seen[0]["FID_INPUT_DATE_2"] == "20260708"
    assert fake_http.params_seen[1]["FID_INPUT_DATE_2"] == "20260706"
