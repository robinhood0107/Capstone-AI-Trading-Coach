from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

NormalizedStatus = Literal["OK", "EMPTY", "PARTIAL", "FAILED"]


@dataclass(frozen=True)
class CorpCode:
    corp_code: str
    corp_name: str
    corp_eng_name: str
    stock_code: str
    modify_date: date | None


@dataclass(frozen=True)
class CompanyProfile:
    corp_code: str
    corp_name: str
    corp_name_eng: str
    stock_name: str
    stock_code: str
    ceo_name: str
    corp_cls: str
    jurir_no: str
    bizr_no: str
    address: str
    homepage_url: str
    ir_url: str
    phone_no: str
    fax_no: str
    industry_code: str
    established_on: date | None
    account_month: str


@dataclass(frozen=True)
class DisclosureListItem:
    corp_cls: str
    corp_name: str
    corp_code: str
    stock_code: str
    report_name: str
    receipt_no: str
    filer_name: str
    receipt_date: date
    remarks: str


@dataclass(frozen=True)
class ObservedDisclosureList:
    items: list[DisclosureListItem]
    raw_observation: RawObservation


@dataclass(frozen=True)
class FinancialStatementRow:
    corp_code: str
    receipt_no: str
    business_year: str
    stock_code: str
    report_code: str
    account_name: str
    fs_div: str
    fs_name: str
    statement_div: str
    statement_name: str
    current_amount: int | None
    currency: str


@dataclass(frozen=True)
class FinancialIndicatorRow:
    corp_code: str
    business_year: str
    report_code: str
    stock_code: str
    settlement_date: date | None
    index_class_code: str
    index_class_name: str
    index_code: str
    index_name: str
    index_value: float | None


@dataclass(frozen=True)
class DisclosureRiskEvent:
    symbol: str
    corp_code: str
    event_code: str
    receipt_no: str
    occurred_on: date
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DisclosureRiskWarning:
    code: str
    event_code: str
    receipt_no: str
    message: str


@dataclass(frozen=True)
class DisclosureRiskScoreResult:
    symbol: str
    as_of: date
    window_from: date
    window_to: date
    score: float
    events: list[DisclosureRiskEvent]
    warnings: list[DisclosureRiskWarning]
    mapping_version: str


@dataclass(frozen=True)
class RawObservation:
    observation_id: str
    source_id: str
    retrieved_at: datetime
    window_from: date | None
    window_to: date | None
    request_fingerprint: str
    payload_hash: str
    raw_storage_uri: str
    normalized_status: NormalizedStatus
    error_code: str | None = None
    error_message: str | None = None
