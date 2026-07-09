from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Protocol

from app.data.opendart.http_client import OpenDARTHttpClient
from app.data.opendart.models import (
    CompanyProfile,
    CorpCode,
    DisclosureListItem,
    DisclosureRiskEvent,
    FinancialIndicatorRow,
    FinancialStatementRow,
    NormalizedStatus,
    ObservedDisclosureList,
)
from app.data.opendart.parsers import (
    parse_audit_opinion_events,
    parse_company_profile,
    parse_corp_code_zip,
    parse_disclosure_list,
    parse_financial_indicator_rows,
    parse_financial_statement_rows,
    parse_major_matter_events,
)
from app.data.opendart.raw_observation import write_raw_observation
from app.data.opendart.settings import OpenDARTSettings

DISCLOSURE_LIST_PATH = "/api/list.json"
CORP_CODE_PATH = "/api/corpCode.xml"
COMPANY_PROFILE_PATH = "/api/company.json"
FINANCIAL_STATEMENT_PATH = "/api/fnlttSinglAcnt.json"
FINANCIAL_INDICATOR_PATH = "/api/fnlttSinglIndx.json"
AUDIT_OPINION_PATH = "/api/accnutAdtorNmNdAdtOpinion.json"
# 주요사항보고서(DS005) 전용 endpoint identity만 위험 이벤트로 승격한다.
# 각 endpoint 이름과 apiId는 OpenDART 개발가이드(DS005)와 대조해 확정했고, report_nm 문자열 매칭은 쓰지 않는다.
MAIN_MATTER_ENDPOINTS = {
    # 자본조달·희석·법적 위험 (S1.2 기존)
    "piicDecsn": "/api/piicDecsn.json",  # 유상증자 결정 (apiId 2020023)
    "cvbdIsDecsn": "/api/cvbdIsDecsn.json",  # 전환사채권 발행결정 (apiId 2020033)
    "lwstLg": "/api/lwstLg.json",  # 소송 등의 제기 (apiId 2020028)
    # going-concern distress·운영중단·자본잠식 신호 (S1.2 고위험 확대)
    "dfOcr": "/api/dfOcr.json",  # 부도발생 (apiId 2020019)
    "ctrcvsBgrq": "/api/ctrcvsBgrq.json",  # 회생절차 개시신청 (apiId 2020021)
    "dsRsOcr": "/api/dsRsOcr.json",  # 해산사유 발생 (apiId 2020022)
    "bnkMngtPcbg": "/api/bnkMngtPcbg.json",  # 채권은행 등의 관리절차 개시 (apiId 2020027)
    "bsnSp": "/api/bsnSp.json",  # 영업정지 (apiId 2020020)
    "crDecsn": "/api/crDecsn.json",  # 감자 결정 (apiId 2020026)
}


class OpenDARTHttpLike(Protocol):
    def get_json(self, path: str, params: dict[str, str]) -> dict[str, Any]: ...

    def get_bytes(self, path: str, params: dict[str, str]) -> bytes: ...


class OpenDARTClient:
    """OpenDART 공식 read-only API를 Python 서비스의 정규화 타입으로 감싸는 client다."""

    def __init__(self, settings: OpenDARTSettings, http_client: OpenDARTHttpLike | None = None) -> None:
        """테스트에서는 fake client를 주입하고 운영에서는 설정 기반 HTTP client를 생성한다."""
        self.settings = settings
        self.http_client = http_client or OpenDARTHttpClient(settings)

    def corp_codes(self) -> list[CorpCode]:
        """OpenDART 고유번호 ZIP/XML을 읽어 주식코드와 DART corp_code를 연결한다."""
        payload = self.http_client.get_bytes(CORP_CODE_PATH, self._with_key({}))
        return parse_corp_code_zip(payload)

    def company_profile(self, corp_code: str) -> CompanyProfile:
        """기업개황은 RAG 설명과 종목 metadata의 공식 기본정보로만 사용한다."""
        response = self.http_client.get_json(COMPANY_PROFILE_PATH, self._with_key({"corp_code": corp_code}))
        return parse_company_profile(response, corp_code=corp_code)

    def disclosure_list(
        self,
        *,
        corp_code: str,
        start: date,
        end: date,
        disclosure_type: str | None = None,
        disclosure_detail_type: str | None = None,
        page_no: int = 1,
        page_count: int = 100,
    ) -> list[DisclosureListItem]:
        """공시검색은 목록 metadata 조회만 수행하며 제목 문자열을 risk score 근거로 쓰지 않는다."""
        params = self._disclosure_list_params(
            corp_code=corp_code,
            start=start,
            end=end,
            disclosure_type=disclosure_type,
            disclosure_detail_type=disclosure_detail_type,
            page_no=page_no,
            page_count=page_count,
        )
        response = self.http_client.get_json(DISCLOSURE_LIST_PATH, params)
        return parse_disclosure_list(response)

    def disclosure_list_with_observation(
        self,
        *,
        corp_code: str,
        start: date,
        end: date,
        disclosure_type: str | None = None,
        disclosure_detail_type: str | None = None,
        page_no: int = 1,
        page_count: int = 100,
        retrieved_at: datetime | None = None,
    ) -> ObservedDisclosureList:
        """공시목록 응답과 RawObservation을 함께 반환해 후속(S1.2+) event aggregator가 원 관측치를 재사용하게 한다."""
        params = self._disclosure_list_params(
            corp_code=corp_code,
            start=start,
            end=end,
            disclosure_type=disclosure_type,
            disclosure_detail_type=disclosure_detail_type,
            page_no=page_no,
            page_count=page_count,
        )
        response = self.http_client.get_json(DISCLOSURE_LIST_PATH, params)
        observation = write_raw_observation(
            data_dir=self.settings.data_dir,
            source_id="opendart_disclosure_list",
            method="GET",
            path=DISCLOSURE_LIST_PATH,
            request_params=params,
            payload=response,
            retrieved_at=retrieved_at or datetime.now(UTC),
            window_from=start,
            window_to=end,
            normalized_status=_normalized_status(response),
            error_code=_error_code(response),
            error_message=_error_message(response),
            known_secrets=[self.settings.api_key],
        )
        # raw 관측치는 parse 전에도 남긴다. 후속(S1.2+) aggregator가 원 응답 상태를 재현해야 하기 때문이다.
        return ObservedDisclosureList(items=parse_disclosure_list(response), raw_observation=observation)

    def financial_statement(self, *, corp_code: str, business_year: str, report_code: str) -> list[FinancialStatementRow]:
        """단일회사 주요계정 조회를 감싸며 전체 XBRL 원문 수집은 S1.2 범위에 넣지 않는다."""
        response = self.http_client.get_json(
            FINANCIAL_STATEMENT_PATH,
            self._with_key({"corp_code": corp_code, "bsns_year": business_year, "reprt_code": report_code}),
        )
        return parse_financial_statement_rows(response, corp_code=corp_code)

    def financial_indicators(
        self,
        *,
        corp_code: str,
        business_year: str,
        report_code: str,
        index_class_code: str,
    ) -> list[FinancialIndicatorRow]:
        """단일회사 주요 재무지표(수익성/안정성/성장성/활동성)를 재무위험·RAG·백테스트 feature 후보로 정규화한다.

        OpenDART `fnlttSinglIndx` endpoint는 `idx_cl_code`(지표분류코드)가 필수이므로 호출부가 분류를 명시하게 강제한다.
        전체 재무제표/XBRL 원문은 저장·재배포 정책이 정해지기 전까지 S1.2 범위에서 제외한다.
        """
        response = self.http_client.get_json(
            FINANCIAL_INDICATOR_PATH,
            self._with_key(
                {
                    "corp_code": corp_code,
                    "bsns_year": business_year,
                    "reprt_code": report_code,
                    "idx_cl_code": index_class_code,
                }
            ),
        )
        return parse_financial_indicator_rows(response, corp_code=corp_code)

    def major_matter_events(
        self,
        *,
        endpoint_id: str,
        symbol: str,
        corp_code: str,
        start: date,
        end: date,
    ) -> list[DisclosureRiskEvent]:
        """주요사항보고서 전용 endpoint identity를 위험 이벤트 코드로 고정한다."""
        path = MAIN_MATTER_ENDPOINTS.get(endpoint_id)
        if path is None:
            raise ValueError(f"Unsupported OpenDART major matter endpoint: {endpoint_id}")
        response = self.http_client.get_json(
            path,
            self._with_key({"corp_code": corp_code, "bgn_de": _format_date(start), "end_de": _format_date(end)}),
        )
        # 주요사항보고서 전용 endpoint identity를 event_code로 삼아 report_nm 문자열 흔들림을 피한다.
        return parse_major_matter_events(
            response,
            event_code=f"OPENDART:{endpoint_id}",
            symbol=symbol,
            fallback_event_date=end,
        )

    def audit_opinion_events(
        self,
        *,
        symbol: str,
        corp_code: str,
        business_year: str,
        report_code: str,
        fallback_event_date: date | None = None,
    ) -> list[DisclosureRiskEvent]:
        """감사의견은 보고서 제목이 아니라 OpenDART의 구조화 `adt_opinion` 필드만 점수화한다."""
        response = self.http_client.get_json(
            AUDIT_OPINION_PATH,
            self._with_key({"corp_code": corp_code, "bsns_year": business_year, "reprt_code": report_code}),
        )
        return parse_audit_opinion_events(response, symbol=symbol, fallback_event_date=fallback_event_date)

    def _with_key(self, params: dict[str, str]) -> dict[str, str]:
        # crtfc_key는 요청 파라미터에만 넣고 fingerprint/log에는 값이 남지 않게 raw_observation에서 별도 마스킹한다.
        return {"crtfc_key": self.settings.api_key or "", **params}

    def _disclosure_list_params(
        self,
        *,
        corp_code: str,
        start: date,
        end: date,
        disclosure_type: str | None,
        disclosure_detail_type: str | None,
        page_no: int,
        page_count: int,
    ) -> dict[str, str]:
        params = self._with_key(
            {
                "corp_code": corp_code,
                "bgn_de": _format_date(start),
                "end_de": _format_date(end),
                "last_reprt_at": "N",
                "sort": "date",
                "sort_mth": "desc",
                "page_no": str(page_no),
                "page_count": str(page_count),
            }
        )
        if disclosure_type:
            params["pblntf_ty"] = disclosure_type
        if disclosure_detail_type:
            params["pblntf_detail_ty"] = disclosure_detail_type
        return params


def _format_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def _normalized_status(response: dict[str, Any]) -> NormalizedStatus:
    status = str(response.get("status"))
    if status == "013":
        return "EMPTY"
    if status not in {"000", "0", "None"}:
        return "FAILED"
    rows = response.get("list") or []
    return "EMPTY" if rows == [] else "OK"


def _error_code(response: dict[str, Any]) -> str | None:
    status = str(response.get("status"))
    return status if _normalized_status(response) == "FAILED" else None


def _error_message(response: dict[str, Any]) -> str | None:
    return str(response.get("message") or "") if _normalized_status(response) == "FAILED" else None
