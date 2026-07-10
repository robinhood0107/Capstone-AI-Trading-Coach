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
    ExecutiveMajorShareholderReportRow,
    FinancialIndicatorRow,
    FinancialStatementRow,
    MajorStockReportRow,
)

SUCCESS_STATUSES = {None, "000", "0", 0}
NO_DATA_STATUS = "013"


class OpenDARTResponseError(RuntimeError):
    pass


class OpenDARTQuotaExceededError(OpenDARTResponseError):
    """HTTP 200 body의 status=020을 transport retry와 분리하는 비재시도 quota 예외다."""

    def __init__(self, status: str, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"OpenDART response failed: {status} {message}")


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


def parse_financial_indicator_batch_rows(response: dict[str, Any]) -> list[FinancialIndicatorRow]:
    """다중회사 주요 재무지표 응답을 회사별 row로 정규화한다.

    단일회사 parser와 달리 corp_code를 파라미터로 받지 않고 각 row의 `corp_code`를 그대로 보존해, 여러 회사 결과가 섞여도 회사 귀속이 유지되게 한다.
    """
    rows = _list_rows(response)
    return [
        FinancialIndicatorRow(
            corp_code=_text(row.get("corp_code")),
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


def parse_major_stock_report_rows(response: dict[str, Any]) -> list[MajorStockReportRow]:
    """대량보유 상황보고(majorstock) 응답을 지분변동 row로 정규화한다.

    지분율/주식수는 콤마·부호가 섞이므로 계산용 숫자로만 변환한다. 이 데이터는 ownership risk·종목 설명 feature 후보이며 S1.2c에서 주문 차단 점수에 연결하지 않는다.
    """
    rows = _list_rows(response)
    return [
        MajorStockReportRow(
            receipt_no=_text(row.get("rcept_no")),
            receipt_date=_required_date(row.get("rcept_dt")),
            corp_code=_text(row.get("corp_code")),
            corp_name=_text(row.get("corp_name")),
            report_type=_text(row.get("report_tp")),
            reporter=_text(row.get("repror")),
            stock_count=_optional_int(row.get("stkqy")),
            stock_count_change=_optional_int(row.get("stkqy_irds")),
            holding_ratio=_optional_float(row.get("stkrt")),
            holding_ratio_change=_optional_float(row.get("stkrt_irds")),
            report_reason=_text(row.get("report_resn")),
        )
        for row in rows
    ]


def parse_executive_major_shareholder_report_rows(
    response: dict[str, Any],
) -> list[ExecutiveMajorShareholderReportRow]:
    """임원ㆍ주요주주 소유보고(elestock) 응답을 소유 row로 정규화한다.

    elestock rcept_dt는 `YYYY-MM-DD` 형식이 올 수 있어 다중 포맷 파서를 재사용한다. insider ownership 설명·feature 후보이며 S1.2c에서 주문 차단 점수에 연결하지 않는다.
    """
    rows = _list_rows(response)
    return [
        ExecutiveMajorShareholderReportRow(
            receipt_no=_text(row.get("rcept_no")),
            receipt_date=_required_date(row.get("rcept_dt")),
            corp_code=_text(row.get("corp_code")),
            corp_name=_text(row.get("corp_name")),
            reporter=_text(row.get("repror")),
            is_registered_executive=_text(row.get("isu_exctv_rgist_at")),
            officer_position=_text(row.get("isu_exctv_ofcps")),
            is_main_shareholder=_text(row.get("isu_main_shrholdr")),
            specific_stock_count=_optional_int(row.get("sp_stock_lmp_cnt")),
            specific_stock_count_change=_optional_int(row.get("sp_stock_lmp_irds_cnt")),
            specific_stock_ratio=_optional_float(row.get("sp_stock_lmp_rate")),
            specific_stock_ratio_change=_optional_float(row.get("sp_stock_lmp_irds_rate")),
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
        message = _text(response.get("message"))
        if str(status) == "020":
            raise OpenDARTQuotaExceededError(status="020", message=message)
        raise OpenDARTResponseError(f"OpenDART response failed: {status} {message}")


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
