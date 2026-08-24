from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from tenacity import wait_none

from app.data.opendart.client import OpenDARTClient
from app.data.opendart.http_client import (
    OpenDARTHttpClient,
    OpenDARTOfflineTransportRequired,
    TokenBucket,
)
from app.data.opendart.parsers import OpenDARTQuotaExceededError, OpenDARTResponseError
from app.data.opendart.settings import OpenDARTSettings


def test_client_disclosure_list_passes_official_filter_params_without_secret() -> None:
    fake_http = FakeHttp({"status": "000", "list": []})
    client = OpenDARTClient(_settings(), fake_http)

    assert "settings" not in vars(client)

    assert (
        client.disclosure_list(
            corp_code="00126380",
            start=date(2026, 6, 9),
            end=date(2026, 7, 9),
            disclosure_type="B",
            disclosure_detail_type="B001",
        )
        == []
    )

    assert fake_http.calls == [
        (
            "/api/list.json",
            {
                "corp_code": "00126380",
                "bgn_de": "20260609",
                "end_de": "20260709",
                "last_reprt_at": "N",
                "pblntf_ty": "B",
                "pblntf_detail_ty": "B001",
                "sort": "date",
                "sort_mth": "desc",
                "page_no": "1",
                "page_count": "100",
            },
        )
    ]


def test_client_default_offline_transport_fails_closed_without_fixture_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http_transport_factories = 0

    def forbidden_http_transport(*_: object, **__: object) -> httpx.BaseTransport:
        nonlocal http_transport_factories
        http_transport_factories += 1
        raise AssertionError("offline client must not construct httpx.HTTPTransport")

    monkeypatch.setattr(httpx, "HTTPTransport", forbidden_http_transport)
    client = OpenDARTClient(_settings(tmp_path))

    with pytest.raises(OpenDARTOfflineTransportRequired, match="injected fixture transport"):
        client.disclosure_list(
            corp_code="00126380",
            start=date(2026, 6, 9),
            end=date(2026, 7, 9),
        )

    assert http_transport_factories == 0


def test_client_major_matter_uses_allowed_endpoint_identity() -> None:
    fake_http = FakeHttp(
        {
            "status": "000",
            "list": [
                {
                    "rcept_no": "20260701000001",
                    "corp_code": "00126380",
                    "corp_cls": "Y",
                    "corp_name": "삼성전자",
                    "bddd": "20260701",
                }
            ],
        }
    )
    client = OpenDARTClient(_settings(), fake_http)

    events = client.major_matter_events(
        endpoint_id="piicDecsn",
        symbol="005930",
        corp_code="00126380",
        start=date(2026, 6, 9),
        end=date(2026, 7, 9),
    )

    assert fake_http.calls[0][0] == "/api/piicDecsn.json"
    assert events[0].event_code == "OPENDART:piicDecsn"
    assert events[0].occurred_on == date(2026, 7, 1)


def test_client_high_severity_major_matter_endpoints_map_to_official_paths_and_identity() -> None:
    # 고위험 DS005 endpoint가 개발가이드 경로와 endpoint 기반 event_code로 고정되는지 검증한다.
    expected = {
        "dfOcr": "/api/dfOcr.json",
        "ctrcvsBgrq": "/api/ctrcvsBgrq.json",
        "dsRsOcr": "/api/dsRsOcr.json",
        "bnkMngtPcbg": "/api/bnkMngtPcbg.json",
        "bsnSp": "/api/bsnSp.json",
        "crDecsn": "/api/crDecsn.json",
    }
    for endpoint_id, path in expected.items():
        fake_http = FakeHttp(
            {
                "status": "000",
                "list": [{"rcept_no": "20260701000001", "corp_code": "00126380"}],
            }
        )
        client = OpenDARTClient(_settings(), fake_http)

        events = client.major_matter_events(
            endpoint_id=endpoint_id,
            symbol="005930",
            corp_code="00126380",
            start=date(2026, 6, 9),
            end=date(2026, 7, 9),
        )

        assert fake_http.calls[0][0] == path
        assert fake_http.calls[0][1]["bgn_de"] == "20260609"
        assert fake_http.calls[0][1]["end_de"] == "20260709"
        assert events[0].event_code == f"OPENDART:{endpoint_id}"
        # distress endpoint는 이벤트별 날짜 필드명이 달라 접수번호로 접수일자를 복원한다.
        assert events[0].occurred_on == date(2026, 7, 1)


def test_client_s1_2b_major_matter_endpoints_map_to_official_paths_and_identity() -> None:
    # S1.2b 확장(희석·복잡상품·reorg) endpoint가 공식 path와 endpoint 기반 event_code로 고정되는지 검증한다.
    expected = {
        "bdwtIsDecsn": "/api/bdwtIsDecsn.json",
        "exbdIsDecsn": "/api/exbdIsDecsn.json",
        "cmpMgDecsn": "/api/cmpMgDecsn.json",
        "cmpDvDecsn": "/api/cmpDvDecsn.json",
        "cmpDvmgDecsn": "/api/cmpDvmgDecsn.json",
        "bsnTrfDecsn": "/api/bsnTrfDecsn.json",
    }
    for endpoint_id, path in expected.items():
        fake_http = FakeHttp(
            {
                "status": "000",
                "list": [
                    {
                        "rcept_no": "20260701000001",
                        "corp_code": "00126380",
                        # report_nm을 일부러 넣어도 event_code/identity 근거로 쓰이지 않아야 한다.
                        "report_nm": "주요사항보고서(문자열은 점수화 근거가 아니다)",
                    }
                ],
            }
        )
        client = OpenDARTClient(_settings(), fake_http)

        events = client.major_matter_events(
            endpoint_id=endpoint_id,
            symbol="005930",
            corp_code="00126380",
            start=date(2026, 6, 9),
            end=date(2026, 7, 9),
        )

        assert fake_http.calls[0][0] == path
        assert fake_http.calls[0][1] == {
            "corp_code": "00126380",
            "bgn_de": "20260609",
            "end_de": "20260709",
        }
        assert events[0].event_code == f"OPENDART:{endpoint_id}"
        assert events[0].occurred_on == date(2026, 7, 1)


def test_client_rejects_unknown_major_matter_endpoint() -> None:
    client = OpenDARTClient(_settings(), FakeHttp({"status": "000", "list": []}))
    with pytest.raises(ValueError):
        client.major_matter_events(
            endpoint_id="not_in_guide",
            symbol="005930",
            corp_code="00126380",
            start=date(2026, 6, 9),
            end=date(2026, 7, 9),
        )


def test_client_financial_indicators_adds_index_class_and_parses_values() -> None:
    fake_http = FakeHttp(
        {
            "status": "000",
            "list": [
                {
                    "reprt_code": "11011",
                    "bsns_year": "2025",
                    "corp_code": "00126380",
                    "stock_code": "005930",
                    "stlm_dt": "2025-12-31",
                    "idx_cl_code": "M220000",
                    "idx_cl_nm": "안정성지표",
                    "idx_code": "M221000",
                    "idx_nm": "부채비율",
                    "idx_val": "45.67",
                }
            ],
        }
    )
    client = OpenDARTClient(_settings(), fake_http)

    rows = client.financial_indicators(
        corp_code="00126380",
        business_year="2025",
        report_code="11011",
        index_class_code="M220000",
    )

    assert fake_http.calls == [
        (
            "/api/fnlttSinglIndx.json",
            {
                "corp_code": "00126380",
                "bsns_year": "2025",
                "reprt_code": "11011",
                "idx_cl_code": "M220000",
            },
        )
    ]
    assert rows[0].index_name == "부채비율"
    assert rows[0].index_value == 45.67
    assert rows[0].settlement_date == date(2025, 12, 31)


def test_client_audit_opinion_events_calls_official_endpoint_with_report_params() -> None:
    fake_http = FakeHttp(
        {
            "status": "000",
            "list": [
                {
                    "rcept_no": "20260401000001",
                    "corp_code": "00999999",
                    "adt_opinion": "의견거절",
                    "stlm_dt": "2026-03-31",
                }
            ],
        }
    )
    client = OpenDARTClient(_settings(), fake_http)

    events = client.audit_opinion_events(
        symbol="105560",
        corp_code="00999999",
        business_year="2025",
        report_code="11011",
    )

    assert fake_http.calls == [
        (
            "/api/accnutAdtorNmNdAdtOpinion.json",
            {
                "corp_code": "00999999",
                "bsns_year": "2025",
                "reprt_code": "11011",
            },
        )
    ]
    assert events[0].event_code == "OPENDART:accnutAdtorNmNdAdtOpinion"
    assert events[0].attributes["adt_opinion"] == "의견거절"


def test_client_company_profile_and_financial_statement_add_required_params() -> None:
    profile_http = FakeHttp(
        {
            "status": "000",
            "corp_name": "삼성전자",
            "corp_name_eng": "SAMSUNG ELECTRONICS",
            "stock_name": "삼성전자",
            "stock_code": "005930",
        }
    )
    financial_http = FakeHttp(
        {
            "status": "000",
            "list": [
                {
                    "rcept_no": "20260331000001",
                    "bsns_year": "2025",
                    "stock_code": "005930",
                    "reprt_code": "11011",
                    "account_nm": "자본총계",
                    "thstrm_amount": "1,000",
                }
            ],
        }
    )

    assert (
        OpenDARTClient(_settings(), profile_http).company_profile("00126380").stock_code == "005930"
    )
    assert (
        OpenDARTClient(_settings(), financial_http)
        .financial_statement(corp_code="00126380", business_year="2025", report_code="11011")[0]
        .current_amount
        == 1000
    )

    assert profile_http.calls == [("/api/company.json", {"corp_code": "00126380"})]
    assert financial_http.calls == [
        (
            "/api/fnlttSinglAcnt.json",
            {
                "corp_code": "00126380",
                "bsns_year": "2025",
                "reprt_code": "11011",
            },
        )
    ]


def test_client_disclosure_list_with_observation_writes_raw_metadata(tmp_path: Path) -> None:
    fake_http = FakeHttp(
        {
            "status": "000",
            "message": "정상",
            "list": [
                {
                    "corp_cls": "Y",
                    "corp_name": "삼성전자",
                    "corp_code": "00126380",
                    "stock_code": "005930",
                    "report_nm": "테스트 공시",
                    "rcept_no": "20260709000001",
                    "flr_nm": "삼성전자",
                    "rcept_dt": "20260709",
                    "rm": "",
                }
            ],
        }
    )
    client = OpenDARTClient(_settings(tmp_path), fake_http)

    result = client.disclosure_list_with_observation(
        corp_code="00126380",
        start=date(2026, 6, 9),
        end=date(2026, 7, 9),
        retrieved_at=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
    )

    assert result.items[0].receipt_no == "20260709000001"
    assert result.raw_observation.source_id == "opendart_disclosure_list"
    assert result.raw_observation.normalized_status == "OK"
    assert result.raw_observation.window_from == date(2026, 6, 9)
    stored = Path(result.raw_observation.raw_storage_uri).read_text(encoding="utf-8")
    assert "crtfc_key" not in stored


def test_client_disclosure_list_with_observation_marks_no_data_as_empty(tmp_path: Path) -> None:
    client = OpenDARTClient(
        _settings(tmp_path),
        FakeHttp({"status": "013", "message": "조회된 데이타가 없습니다."}),
    )

    result = client.disclosure_list_with_observation(
        corp_code="00126380",
        start=date(2026, 6, 9),
        end=date(2026, 7, 9),
        retrieved_at=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
    )

    assert result.items == []
    assert result.raw_observation.normalized_status == "EMPTY"


def test_client_status_020_raises_non_retryable_quota_error_once(tmp_path: Path) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            json={"status": "020", "message": "요청 제한을 초과하였습니다."},
        )

    settings = _settings(tmp_path)
    with OpenDARTHttpClient(
        settings,
        transport=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(rate_per_second=1000),
        retry_wait=wait_none(),
    ) as http_client:
        client = OpenDARTClient(settings, http_client)

        with pytest.raises(OpenDARTQuotaExceededError) as exc_info:
            client.disclosure_list(
                corp_code="00126380",
                start=date(2026, 7, 1),
                end=date(2026, 7, 10),
            )

    error = exc_info.value
    assert isinstance(error, OpenDARTResponseError)
    assert error.status == "020"
    assert error.message == "요청 제한을 초과하였습니다."
    assert attempts == 1


def test_client_s1_2c_financial_indicators_batch_joins_corp_codes() -> None:
    # 다중회사 주요 재무지표는 corp_code를 comma-join한 복수조회 파라미터로 보내야 한다.
    fake_http = FakeHttp(
        {
            "status": "000",
            "list": [
                {
                    "corp_code": "00126380",
                    "idx_cl_code": "M220000",
                    "idx_nm": "부채비율",
                    "idx_val": "45.67",
                },
                {
                    "corp_code": "00164779",
                    "idx_cl_code": "M220000",
                    "idx_nm": "부채비율",
                    "idx_val": "88.10",
                },
            ],
        }
    )
    client = OpenDARTClient(_settings(), fake_http)

    rows = client.financial_indicators_batch(
        corp_codes=["00126380", "00164779"],
        business_year="2025",
        report_code="11011",
        index_class_code="M220000",
    )

    assert fake_http.calls == [
        (
            "/api/fnlttCmpnyIndx.json",
            {
                "corp_code": "00126380,00164779",
                "bsns_year": "2025",
                "reprt_code": "11011",
                "idx_cl_code": "M220000",
            },
        )
    ]
    # batch parser는 파라미터가 아니라 row별 corp_code를 보존해야 한다.
    assert [row.corp_code for row in rows] == ["00126380", "00164779"]


def test_client_s1_2c_financial_indicators_batch_rejects_empty_corp_codes() -> None:
    client = OpenDARTClient(_settings(), FakeHttp({"status": "000", "list": []}))
    with pytest.raises(ValueError):
        client.financial_indicators_batch(
            corp_codes=[],
            business_year="2025",
            report_code="11011",
            index_class_code="M220000",
        )


def test_client_s1_2c_ownership_disclosure_endpoints_pass_corp_code_without_secret() -> None:
    major_http = FakeHttp(
        {
            "status": "000",
            "list": [
                {
                    "rcept_no": "20260701000001",
                    "rcept_dt": "20260701",
                    "corp_code": "00126380",
                    "corp_name": "삼성전자",
                    "report_tp": "대량보유",
                    "repror": "국민연금공단",
                    "stkqy": "1,000,000",
                    "stkrt": "5.01",
                    "stkrt_irds": "0.30",
                }
            ],
        }
    )
    ele_http = FakeHttp(
        {
            "status": "000",
            "list": [
                {
                    "rcept_no": "20260702000002",
                    "rcept_dt": "2026-07-02",
                    "corp_code": "00126380",
                    "corp_name": "삼성전자",
                    "repror": "대표이사",
                    "isu_main_shrholdr": "해당",
                    "sp_stock_lmp_cnt": "12,345",
                    "sp_stock_lmp_rate": "0.12",
                }
            ],
        }
    )

    major = OpenDARTClient(_settings(), major_http).major_stock_reports(corp_code="00126380")
    ele = OpenDARTClient(_settings(), ele_http).executive_major_shareholder_reports(
        corp_code="00126380"
    )

    assert major_http.calls == [("/api/majorstock.json", {"corp_code": "00126380"})]
    assert ele_http.calls == [("/api/elestock.json", {"corp_code": "00126380"})]
    assert major[0].holding_ratio == 5.01
    assert major[0].receipt_date == date(2026, 7, 1)
    assert ele[0].specific_stock_count == 12345
    assert ele[0].receipt_date == date(2026, 7, 2)


class FakeHttp:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        self.calls.append((path, params))
        return self.response

    def get_bytes(self, path: str, params: dict[str, str]) -> bytes:
        self.calls.append((path, params))
        return b""


def _settings(data_dir: Path | None = None) -> OpenDARTSettings:
    kwargs = {"opendart_offline": True, "_env_file": None}
    if data_dir is not None:
        kwargs["opendart_data_dir"] = data_dir
    return OpenDARTSettings(**kwargs)
