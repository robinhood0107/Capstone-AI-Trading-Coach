from __future__ import annotations

from datetime import date
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.data.opendart.models import DisclosureRiskEvent
from app.data.opendart.parsers import (
    OpenDARTResponseError,
    parse_audit_opinion_events,
    parse_company_profile,
    parse_corp_code_zip,
    parse_disclosure_list,
    parse_financial_indicator_rows,
    parse_financial_statement_rows,
    parse_major_matter_events,
)


def test_parse_corp_code_zip_reads_official_xml_shape() -> None:
    zip_bytes = _corp_code_zip(
        """
        <result>
          <list>
            <corp_code>00126380</corp_code>
            <corp_name>삼성전자</corp_name>
            <corp_eng_name>SAMSUNG ELECTRONICS</corp_eng_name>
            <stock_code>005930</stock_code>
            <modify_date>20260701</modify_date>
          </list>
          <list>
            <corp_code>00164779</corp_code>
            <corp_name>현대자동차</corp_name>
            <corp_eng_name>HYUNDAI MOTOR</corp_eng_name>
            <stock_code>005380</stock_code>
            <modify_date>20260701</modify_date>
          </list>
        </result>
        """
    )

    rows = parse_corp_code_zip(zip_bytes)

    assert rows[0].corp_code == "00126380"
    assert rows[0].stock_code == "005930"
    assert rows[1].corp_name == "현대자동차"


def test_parse_disclosure_list_keeps_report_name_as_metadata_only() -> None:
    response = {
        "status": "000",
        "message": "정상",
        "list": [
            {
                "corp_cls": "Y",
                "corp_name": "삼성전자",
                "corp_code": "00126380",
                "stock_code": "005930",
                "report_nm": "테스트 보고서명",
                "rcept_no": "20260709000001",
                "flr_nm": "삼성전자",
                "rcept_dt": "20260709",
                "rm": "",
            }
        ],
    }

    rows = parse_disclosure_list(response)

    assert rows == [
        rows[0],
    ]
    assert rows[0].report_name == "테스트 보고서명"
    assert rows[0].receipt_date == date(2026, 7, 9)


def test_parse_company_profile_and_financial_rows() -> None:
    profile = parse_company_profile(
        {
            "status": "000",
            "corp_name": "삼성전자",
            "corp_name_eng": "SAMSUNG ELECTRONICS",
            "stock_name": "삼성전자",
            "stock_code": "005930",
            "ceo_nm": "대표",
            "corp_cls": "Y",
            "jurir_no": "1101110000000",
            "bizr_no": "0000000000",
            "adres": "서울",
            "hm_url": "https://example.invalid",
            "ir_url": "",
            "phn_no": "02-0000-0000",
            "fax_no": "",
            "induty_code": "264",
            "est_dt": "19690113",
            "acc_mt": "12",
        },
        corp_code="00126380",
    )
    rows = parse_financial_statement_rows(
        {
            "status": "000",
            "list": [
                {
                    "rcept_no": "20260331000001",
                    "bsns_year": "2025",
                    "stock_code": "005930",
                    "reprt_code": "11011",
                    "account_nm": "자본총계",
                    "fs_div": "CFS",
                    "fs_nm": "연결재무제표",
                    "sj_div": "BS",
                    "sj_nm": "재무상태표",
                    "thstrm_amount": "1,234,567",
                    "currency": "KRW",
                }
            ],
        },
        corp_code="00126380",
    )

    assert profile.corp_code == "00126380"
    assert profile.stock_code == "005930"
    assert rows[0].account_name == "자본총계"
    assert rows[0].current_amount == 1_234_567


def test_parse_financial_indicator_rows_normalizes_index_values() -> None:
    rows = parse_financial_indicator_rows(
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
                },
                {
                    "reprt_code": "11011",
                    "bsns_year": "2025",
                    "corp_code": "00126380",
                    "stock_code": "005930",
                    "stlm_dt": "2025-12-31",
                    "idx_cl_code": "M230000",
                    "idx_cl_nm": "성장성지표",
                    "idx_code": "M231000",
                    "idx_nm": "매출액증가율",
                    "idx_val": "-",
                },
            ],
        },
        corp_code="00126380",
    )

    assert rows[0].index_value == 45.67
    assert rows[0].index_class_name == "안정성지표"
    assert rows[0].settlement_date == date(2025, 12, 31)
    # 결측/비수치 지표는 None으로 남겨 downstream이 0.0과 구분하게 한다.
    assert rows[1].index_value is None


def test_event_date_falls_back_to_receipt_number_for_distress_endpoint() -> None:
    # 부도발생(dfOcr)처럼 이벤트 날짜 필드명이 표준 키에 없으면 접수번호 앞 8자리로 접수일자를 복원한다.
    events = parse_major_matter_events(
        {
            "status": "000",
            "list": [
                {
                    "rcept_no": "20260705000007",
                    "corp_code": "00999999",
                    "df_cn": "당좌거래 정지",
                    "dfd": "2026-07-04",
                }
            ],
        },
        event_code="OPENDART:dfOcr",
        symbol="900000",
        fallback_event_date=None,
    )

    assert events[0].occurred_on == date(2026, 7, 5)
    assert events[0].event_code == "OPENDART:dfOcr"


def test_parse_major_matter_events_uses_endpoint_identity_not_report_title() -> None:
    response = {
        "status": "000",
        "list": [
            {
                "rcept_no": "20260701000001",
                "corp_cls": "Y",
                "corp_code": "00164779",
                "corp_name": "현대자동차",
                "icnm": "손해배상",
                "lgd": "20260701",
                "report_nm": "문자열은 점수화 근거가 아니다",
            }
        ],
    }

    events = parse_major_matter_events(
        response,
        event_code="OPENDART:lwstLg",
        symbol="005380",
        fallback_event_date=date(2026, 7, 9),
    )

    assert events == [
        DisclosureRiskEvent(
            symbol="005380",
            corp_code="00164779",
            event_code="OPENDART:lwstLg",
            receipt_no="20260701000001",
            occurred_on=date(2026, 7, 1),
            attributes={"corp_cls": "Y", "corp_name": "현대자동차", "icnm": "손해배상"},
        )
    ]


def test_parse_audit_opinion_events_keeps_structured_opinion_field() -> None:
    events = parse_audit_opinion_events(
        {
            "status": "000",
            "list": [
                {
                    "rcept_no": "20260401000001",
                    "corp_cls": "K",
                    "corp_code": "00999999",
                    "corp_name": "테스트",
                    "bsns_year": "2025",
                    "adt_opinion": "의견거절",
                    "stlm_dt": "2026-03-31",
                }
            ],
        },
        symbol="105560",
        fallback_event_date=date(2026, 4, 1),
    )

    assert events[0].event_code == "OPENDART:accnutAdtorNmNdAdtOpinion"
    assert events[0].occurred_on == date(2026, 3, 31)
    assert events[0].attributes["adt_opinion"] == "의견거절"


def test_no_data_status_returns_empty_list() -> None:
    assert parse_disclosure_list({"status": "013", "message": "조회된 데이타가 없습니다."}) == []


def test_error_status_raises_masked_open_dart_error() -> None:
    with pytest.raises(OpenDARTResponseError) as exc_info:
        parse_disclosure_list({"status": "010", "message": "등록되지 않은 키입니다."})

    assert "010" in str(exc_info.value)
    assert "등록되지 않은 키" in str(exc_info.value)


def _corp_code_zip(xml_text: str) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("CORPCODE.xml", xml_text)
    return buffer.getvalue()
