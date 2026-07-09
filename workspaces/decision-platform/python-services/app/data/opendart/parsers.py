from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
from typing import Any
from xml.etree import ElementTree
from zipfile import ZipFile

from app.data.opendart.models import (
    CompanyProfile,
    CorpCode,
    DisclosureListItem,
    DisclosureRiskEvent,
    FinancialIndicatorRow,
    FinancialStatementRow,
)

SUCCESS_STATUSES = {None, "000", "0", 0}
NO_DATA_STATUS = "013"


class OpenDARTResponseError(RuntimeError):
    pass


def parse_corp_code_zip(payload: bytes) -> list[CorpCode]:
    """OpenDART가 배포하는 corpCode.xml ZIP을 공식 corp_code lookup으로 변환한다."""
    with ZipFile(BytesIO(payload)) as archive:
        xml_name = next(name for name in archive.namelist() if name.lower().endswith(".xml"))
        xml_payload = archive.read(xml_name)
    root = ElementTree.fromstring(xml_payload)
    return [
        CorpCode(
            corp_code=_xml_text(row, "corp_code"),
            corp_name=_xml_text(row, "corp_name"),
            corp_eng_name=_xml_text(row, "corp_eng_name"),
            stock_code=_xml_text(row, "stock_code"),
            modify_date=_optional_date(_xml_text(row, "modify_date")),
        )
        for row in root.findall(".//list")
    ]


def parse_company_profile(response: dict[str, Any], corp_code: str) -> CompanyProfile:
    """기업개황 응답에서 설명/RAG에 필요한 공개 기본정보만 정규화한다."""
    _ensure_success(response)
    return CompanyProfile(
        corp_code=corp_code,
        corp_name=_text(response.get("corp_name")),
        corp_name_eng=_text(response.get("corp_name_eng")),
        stock_name=_text(response.get("stock_name")),
        stock_code=_text(response.get("stock_code")),
        ceo_name=_text(response.get("ceo_nm")),
        corp_cls=_text(response.get("corp_cls")),
        jurir_no=_text(response.get("jurir_no")),
        bizr_no=_text(response.get("bizr_no")),
        address=_text(response.get("adres")),
        homepage_url=_text(response.get("hm_url")),
        ir_url=_text(response.get("ir_url")),
        phone_no=_text(response.get("phn_no")),
        fax_no=_text(response.get("fax_no")),
        industry_code=_text(response.get("induty_code")),
        established_on=_optional_date(response.get("est_dt")),
        account_month=_text(response.get("acc_mt")),
    )


def parse_disclosure_list(response: dict[str, Any]) -> list[DisclosureListItem]:
    """공시검색 응답을 목록 metadata로만 파싱하고 risk event로 직접 승격하지 않는다."""
    rows = _list_rows(response)
    return [
        DisclosureListItem(
            corp_cls=_text(row.get("corp_cls")),
            corp_name=_text(row.get("corp_name")),
            corp_code=_text(row.get("corp_code")),
            stock_code=_text(row.get("stock_code")),
            report_name=_text(row.get("report_nm")),
            receipt_no=_text(row.get("rcept_no")),
            filer_name=_text(row.get("flr_nm")),
            receipt_date=_required_date(row.get("rcept_dt")),
            remarks=_text(row.get("rm")),
        )
        for row in rows
    ]


def parse_financial_statement_rows(response: dict[str, Any], corp_code: str) -> list[FinancialStatementRow]:
    """단일회사 주요계정 응답의 금액 문자열을 계산 가능한 정수 필드로 정규화한다."""
    rows = _list_rows(response)
    return [
        FinancialStatementRow(
            corp_code=corp_code,
            receipt_no=_text(row.get("rcept_no")),
            business_year=_text(row.get("bsns_year")),
            stock_code=_text(row.get("stock_code")),
            report_code=_text(row.get("reprt_code")),
            account_name=_text(row.get("account_nm")),
            fs_div=_text(row.get("fs_div")),
            fs_name=_text(row.get("fs_nm")),
            statement_div=_text(row.get("sj_div")),
            statement_name=_text(row.get("sj_nm")),
            current_amount=_optional_int(row.get("thstrm_amount")),
            currency=_text(row.get("currency")),
        )
        for row in rows
    ]


def parse_financial_indicator_rows(response: dict[str, Any], corp_code: str) -> list[FinancialIndicatorRow]:
    """단일회사 주요 재무지표 응답을 계산 가능한 float 지표 row로 정규화한다.

    `idx_val`은 백분율/음수 문자열이 섞이므로 계산용 float로만 변환하고, 파싱 실패 값은 None으로 남겨 downstream이 결측을 구분하게 한다.
    """
    rows = _list_rows(response)
    return [
        FinancialIndicatorRow(
            corp_code=corp_code,
            business_year=_text(row.get("bsns_year")),
            report_code=_text(row.get("reprt_code")),
            stock_code=_text(row.get("stock_code")),
            settlement_date=_optional_date(row.get("stlm_dt")),
            index_class_code=_text(row.get("idx_cl_code")),
            index_class_name=_text(row.get("idx_cl_nm")),
            index_code=_text(row.get("idx_code")),
            index_name=_text(row.get("idx_nm")),
            index_value=_optional_float(row.get("idx_val")),
        )
        for row in rows
    ]


def parse_major_matter_events(
    response: dict[str, Any],
    *,
    event_code: str,
    symbol: str,
    fallback_event_date: date | None,
) -> list[DisclosureRiskEvent]:
    """전용 주요사항 endpoint에서 온 row를 endpoint 기반 위험 이벤트로 변환한다."""
    rows = _list_rows(response)
    return [
        DisclosureRiskEvent(
            symbol=symbol,
            corp_code=_text(row.get("corp_code")),
            event_code=event_code,
            receipt_no=_text(row.get("rcept_no")),
            occurred_on=_event_date(row, fallback_event_date),
            attributes=_event_attributes(row),
        )
        for row in rows
    ]


def parse_audit_opinion_events(
    response: dict[str, Any],
    *,
    symbol: str,
    fallback_event_date: date | None,
) -> list[DisclosureRiskEvent]:
    """감사의견 endpoint 응답을 `adt_opinion` 조건부 scorer 입력으로 넘긴다."""
    return parse_major_matter_events(
        response,
        event_code="OPENDART:accnutAdtorNmNdAdtOpinion",
        symbol=symbol,
        fallback_event_date=fallback_event_date,
    )


def _list_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    if str(response.get("status")) == NO_DATA_STATUS:
        return []
    _ensure_success(response)
    rows = response.get("list") or []
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        raise OpenDARTResponseError("OpenDART list response must contain a list")
    return [row for row in rows if isinstance(row, dict)]


def _ensure_success(response: dict[str, Any]) -> None:
    status = response.get("status")
    if status not in SUCCESS_STATUSES:
        # OpenDART 오류는 status/message만 노출한다. 요청 key나 raw body는 예외 문자열에 싣지 않는다.
        raise OpenDARTResponseError(f"OpenDART response failed: {status} {_text(response.get('message'))}")


def _event_date(row: dict[str, Any], fallback_event_date: date | None) -> date:
    for key in ("rcept_dt", "bddd", "lgd", "cfd", "stlm_dt"):
        value = row.get(key)
        if value not in (None, ""):
            return _required_date(value)
    # DS005 distress endpoint(부도/영업정지 등)는 이벤트별 날짜 필드명이 제각각이라 공통 접수번호로 접수일자를 복원한다.
    # rcept_no 앞 8자리는 접수일자(YYYYMMDD)라는 구조 규약이므로 report_nm 문자열 매칭 없이도 window 판정이 가능하다.
    receipt_no = _text(row.get("rcept_no"))
    if len(receipt_no) >= 8 and receipt_no[:8].isdigit():
        return _required_date(receipt_no[:8])
    if fallback_event_date is not None:
        return fallback_event_date
    raise OpenDARTResponseError("OpenDART event row did not include a usable event date")


def _event_attributes(row: dict[str, Any]) -> dict[str, str]:
    excluded = {
        "rcept_no",
        "corp_code",
        "rcept_dt",
        "bddd",
        "lgd",
        "cfd",
        "stlm_dt",
        "report_nm",
    }
    # report_nm은 metadata일 뿐 점수화 근거가 아니다. event attribute에서도 제외해 회귀를 어렵게 만든다.
    return {key: _text(value) for key, value in row.items() if key not in excluded and value not in (None, "")}


def _xml_text(row: ElementTree.Element, tag: str) -> str:
    child = row.find(tag)
    return "" if child is None or child.text is None else child.text.strip()


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _optional_int(value: Any) -> int | None:
    text = _text(value).replace(",", "")
    if text in {"", "-"}:
        return None
    return int(text)


def _optional_float(value: Any) -> float | None:
    # 재무지표 값은 콤마/백분율 기호가 섞일 수 있어 계산용 float로만 정규화하고, 비수치는 결측(None)으로 둔다.
    text = _text(value).replace(",", "").replace("%", "")
    if text in {"", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _optional_date(value: Any) -> date | None:
    text = _text(value)
    if not text:
        return None
    return _required_date(text)


def _required_date(value: Any) -> date:
    text = _text(value)
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise OpenDARTResponseError(f"Invalid OpenDART date: {text}")
