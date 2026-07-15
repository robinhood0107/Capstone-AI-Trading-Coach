from __future__ import annotations

from app.data.krx.catalog import (
    ENABLED_UNIVERSE_ENDPOINTS,
    KOSDAQ_DAILY,
    KOSPI_DAILY,
    KrxEndpoint,
)


def test_universe_catalog_enables_only_the_two_approved_stock_daily_endpoints() -> None:
    assert ENABLED_UNIVERSE_ENDPOINTS == (KOSPI_DAILY, KOSDAQ_DAILY)
    assert all(isinstance(endpoint, KrxEndpoint) for endpoint in ENABLED_UNIVERSE_ENDPOINTS)
    assert [(endpoint.market, endpoint.path) for endpoint in ENABLED_UNIVERSE_ENDPOINTS] == [
        ("KOSPI", "/svc/apis/sto/stk_bydd_trd.json"),
        ("KOSDAQ", "/svc/apis/sto/ksq_bydd_trd.json"),
    ]


def test_enabled_endpoints_use_only_the_official_date_parameter_and_response_block() -> None:
    for endpoint in ENABLED_UNIVERSE_ENDPOINTS:
        assert endpoint.request_parameter == "basDd"
        assert endpoint.response_block == "OutBlock_1"
        assert endpoint.name
        assert "http" not in endpoint.name.lower()
        assert "auth" not in repr(endpoint).lower()
