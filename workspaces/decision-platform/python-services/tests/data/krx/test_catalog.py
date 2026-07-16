from __future__ import annotations

from app.data.krx.catalog import (
    ENABLED_UNIVERSE_ENDPOINTS,
    KRX_SERVICE_PLAN,
    KOSDAQ_DAILY,
    KOSPI_DAILY,
    KrxEndpoint,
)


_EXPECTED_SERVICE_PLAN = (
    ("지수", "krx_dd_trd", "LATER"),
    ("지수", "kospi_dd_trd", "NEXT"),
    ("지수", "kosdaq_dd_trd", "NEXT"),
    ("지수", "bon_dd_trd", "LATER"),
    ("지수", "drvprod_dd_trd", "LATER"),
    ("주식", "stk_bydd_trd", "NOW"),
    ("주식", "ksq_bydd_trd", "NOW"),
    ("주식", "knx_bydd_trd", "EXCLUDE"),
    ("주식", "sw_bydd_trd", "EXCLUDE"),
    ("주식", "sr_bydd_trd", "EXCLUDE"),
    ("주식", "stk_isu_base_info", "NEXT"),
    ("주식", "ksq_isu_base_info", "NEXT"),
    ("주식", "knx_isu_base_info", "EXCLUDE"),
    ("증권상품", "etf_bydd_trd", "NEXT"),
    ("증권상품", "etn_bydd_trd", "NEXT"),
    ("증권상품", "elw_bydd_trd", "LATER"),
    ("채권", "kts_bydd_trd", "LATER"),
    ("채권", "bnd_bydd_trd", "LATER"),
    ("채권", "smb_bydd_trd", "LATER"),
    ("파생상품", "fut_bydd_trd", "LATER"),
    ("파생상품", "eqsfu_stk_bydd_trd", "LATER"),
    ("파생상품", "eqkfu_ksq_bydd_trd", "LATER"),
    ("파생상품", "opt_bydd_trd", "LATER"),
    ("파생상품", "eqsop_bydd_trd", "LATER"),
    ("파생상품", "eqkop_bydd_trd", "LATER"),
    ("일반상품", "oil_bydd_trd", "EXCLUDE"),
    ("일반상품", "gold_bydd_trd", "NEXT"),
    ("일반상품", "ets_bydd_trd", "EXCLUDE"),
    ("ESG", "esg_etp_info", "EXCLUDE"),
    ("ESG", "sri_bond_info", "EXCLUDE"),
    ("ESG", "esg_index_info", "EXCLUDE"),
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


def test_official_service_plan_has_exactly_seven_categories_and_thirty_one_unique_ids() -> None:
    observed = tuple(
        (service.category, service.api_id, service.status) for service in KRX_SERVICE_PLAN
    )

    assert observed == _EXPECTED_SERVICE_PLAN
    assert len({service.category for service in KRX_SERVICE_PLAN}) == 7
    assert len({service.api_id for service in KRX_SERVICE_PLAN}) == 31


def test_only_now_service_ids_have_runtime_endpoints() -> None:
    expected_now = {service.api_id for service in KRX_SERVICE_PLAN if service.status == "NOW"}
    runtime_ids = {
        endpoint.path.rsplit("/", 1)[-1].removesuffix(".json")
        for endpoint in ENABLED_UNIVERSE_ENDPOINTS
    }

    assert expected_now == {"stk_bydd_trd", "ksq_bydd_trd"}
    assert runtime_ids == expected_now
