"""KIS_MOCK online client의 sanitized balance/buyable/execution application adapter."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, NoReturn

from app.brokerage.kis_mock_online_client import (
    BALANCE_PATH,
    BUYABLE_PATH,
    EXECUTIONS_PATH,
    KISMockBrokerageHttpClient,
    KISMockFailureReason,
    MOCK_BALANCE_TR_ID,
    MOCK_BUYABLE_TR_ID,
    MOCK_EXECUTIONS_ARCHIVE_TR_ID,
    MOCK_EXECUTIONS_RECENT_TR_ID,
)
from app.brokerage.mock_order_reference_store import MockProviderOrderReference
from app.generated import brokerage_pb2

_SYMBOL = re.compile(r"^[0-9]{6}$")
_DATE = re.compile(r"^[0-9]{8}$")
_TIME = re.compile(r"^[0-9]{6}$")
_MAX_BIGINT = 9_223_372_036_854_775_807
_MAX_POSITIONS = 1_000
_MAX_MOCK_EXECUTION_ROWS = 15


@dataclass(frozen=True, slots=True)
class MockExecutionSnapshot:
    """raw ODNO 없이 한 주문의 최신 KIS cumulative execution만 보존한다."""

    provider_exec_ref_hash: str
    symbol: str
    cumulative_quantity: int
    leaves_quantity: int
    average_fill_price_krw: int | None
    cancelled: bool
    rejected: bool
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class KISMockBalanceSourceProbe:
    """연결성 진단용 sanitized source shape이며 완전한 risk balance를 표현하지 않는다."""

    account_id: str
    cash_krw: int
    portfolio_equity_krw: int
    positions: tuple[tuple[str, int, int], ...]


class KISMockProjectionError(ValueError):
    """provider 원문 없이 sanitized projection의 exact validation leaf만 보존한다."""

    def __init__(self, reason: KISMockFailureReason, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason.value
        self.provider_code: str | None = None
        self.http_status: int | None = None


class KISMockOnlineBalanceReader:
    """KIS raw balance를 bounded protobuf projection으로 즉시 축약한다."""

    def __init__(self, client: KISMockBrokerageHttpClient) -> None:
        self._client = client

    def balance(self, account_id: str) -> brokerage_pb2.GetMockBalanceResponse | None:
        """trusted margin/catalog enrichment 없이는 owner-facing balance를 만들지 않는다."""
        del account_id
        # KIS cash balance 원문만으로 margin과 gold ETF/ETN 분류를 추정하면 risk 계산이 약화된다.
        raise KISMockProjectionError(
            KISMockFailureReason.BALANCE_RISK_FIELDS_UNAVAILABLE,
            "KIS mock balance risk fields are unavailable",
        )

    def probe_balance_source(self, account_id: str) -> KISMockBalanceSourceProbe:
        """exact-approved 진단에서 cash/equity/position source shape만 bounded 검증한다."""
        payload = self._client.request(
            "GET",
            BALANCE_PATH,
            MOCK_BALANCE_TR_ID,
            params={
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )
        if payload.get("ctx_area_fk100") or payload.get("ctx_area_nk100"):
            raise KISMockProjectionError(
                KISMockFailureReason.BALANCE_PAGINATION_REQUIRED,
                "KIS mock balance response requires another bounded page",
            )
        positions = _positions(payload.get("output1"))
        summary = _single_object(
            payload.get("output2"),
            "balance summary",
            reason=KISMockFailureReason.BALANCE_SUMMARY_INVALID,
        )
        return KISMockBalanceSourceProbe(
            account_id=account_id,
            cash_krw=_nonnegative(
                summary.get("dnca_tot_amt"),
                "cash",
                reason=KISMockFailureReason.BALANCE_CASH_INVALID,
            ),
            portfolio_equity_krw=_nonnegative(
                summary.get("tot_evlu_amt"),
                "portfolio equity",
                reason=KISMockFailureReason.BALANCE_EQUITY_INVALID,
            ),
            positions=positions,
        )

    def buyable(
        self,
        account_id: str,
        symbol: str,
        estimated_price_krw: int,
    ) -> brokerage_pb2.GetMockBuyableResponse | None:
        if _SYMBOL.fullmatch(symbol) is None or estimated_price_krw <= 0:
            raise ValueError("KIS mock buyable input is invalid")
        payload = self._client.request(
            "GET",
            BUYABLE_PATH,
            MOCK_BUYABLE_TR_ID,
            params={
                "PDNO": symbol,
                "ORD_UNPR": str(estimated_price_krw),
                "ORD_DVSN": "00",
                "CMA_EVLU_AMT_ICLD_YN": "N",
                "OVRS_ICLD_YN": "N",
            },
        )
        output = _single_object(payload.get("output"), "buyable")
        return brokerage_pb2.GetMockBuyableResponse(
            account_id=account_id,
            symbol=symbol,
            estimated_price_krw=estimated_price_krw,
            buyable_quantity=_nonnegative(output.get("max_buy_qty"), "buyable quantity"),
            buyable_amount_krw=_nonnegative(output.get("max_buy_amt"), "buyable amount"),
            cash_krw=_nonnegative(output.get("ord_psbl_cash"), "buyable cash"),
            observed_at=_now_text(),
            source_version="kis-mock-buyable-v1",
        )


class KISMockExecutionReader:
    """한 provider order reference를 exact date window의 최신 cumulative row로 대사한다."""

    def __init__(self, client: KISMockBrokerageHttpClient) -> None:
        self._client = client

    def read(
        self,
        *,
        reference: MockProviderOrderReference,
        start: date,
        end: date,
        recent: bool,
    ) -> MockExecutionSnapshot:
        if start > end or (end - start).days > 31:
            raise ValueError("KIS mock execution window is invalid")
        tr_id = (
            MOCK_EXECUTIONS_RECENT_TR_ID
            if recent
            else MOCK_EXECUTIONS_ARCHIVE_TR_ID
        )
        payload = self._client.request(
            "GET",
            EXECUTIONS_PATH,
            tr_id,
            params={
                "INQR_STRT_DT": start.strftime("%Y%m%d"),
                "INQR_END_DT": end.strftime("%Y%m%d"),
                "SLL_BUY_DVSN_CD": "00",
                "INQR_DVSN": "00",
                "PDNO": "",
                "CCLD_DVSN": "00",
                "ORD_GNO_BRNO": reference.provider_org_no,
                "ODNO": reference.provider_order_no,
                "INQR_DVSN_3": "00",
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
                "EXCG_ID_DVSN_CD": "KRX",
            },
        )
        if payload.get("ctx_area_fk100") or payload.get("ctx_area_nk100"):
            raise ValueError("KIS mock execution response requires another bounded page")
        rows = payload.get("output1")
        if not isinstance(rows, list) or not 1 <= len(rows) <= _MAX_MOCK_EXECUTION_ROWS:
            raise ValueError("KIS mock execution response is incomplete")
        matches = [
            row
            for row in rows
            if isinstance(row, dict) and row.get("odno") == reference.provider_order_no
        ]
        if len(matches) != 1:
            raise ValueError("KIS mock execution order match is not unique")
        row = matches[0]
        symbol = row.get("pdno")
        if not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None:
            raise ValueError("KIS mock execution symbol is invalid")
        cumulative = _nonnegative(row.get("tot_ccld_qty"), "cumulative quantity")
        leaves = _nonnegative(row.get("rmn_qty"), "leaves quantity")
        if cumulative + leaves > reference.quantity:
            raise ValueError("KIS mock execution quantity invariant failed")
        average = _nonnegative(row.get("avg_prvs"), "average fill price")
        if cumulative == 0:
            average_value: int | None = None
        elif average > 0:
            average_value = average
        else:
            raise ValueError("KIS mock execution average price is invalid")
        order_date = row.get("ord_dt")
        order_time = row.get("ord_tmd")
        if (
            not isinstance(order_date, str)
            or _DATE.fullmatch(order_date) is None
            or not isinstance(order_time, str)
            or _TIME.fullmatch(order_time) is None
        ):
            raise ValueError("KIS mock execution timestamp is invalid")
        observed_at = datetime.strptime(
            f"{order_date}{order_time}",
            "%Y%m%d%H%M%S",
        ).replace(tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Seoul")).astimezone(UTC)
        cancelled_quantity = _nonnegative(
            row.get("cnc_cfrm_qty"),
            "cancelled quantity",
        )
        rejected_quantity = _nonnegative(row.get("rjct_qty"), "rejected quantity")
        identity = (
            "kis-mock-execution/v1\0"
            f"{reference.provider_order_no}\0{cumulative}\0{leaves}\0"
            f"{average_value or 0}\0{order_date}\0{order_time}"
        )
        return MockExecutionSnapshot(
            provider_exec_ref_hash=hashlib.sha256(identity.encode()).hexdigest(),
            symbol=symbol,
            cumulative_quantity=cumulative,
            leaves_quantity=leaves,
            average_fill_price_krw=average_value,
            cancelled=cancelled_quantity > 0 or str(row.get("cncl_yn") or "") == "Y",
            rejected=rejected_quantity > 0,
            observed_at=observed_at,
        )


def _positions(value: object) -> tuple[tuple[str, int, int], ...]:
    if not isinstance(value, list) or len(value) > _MAX_POSITIONS:
        raise KISMockProjectionError(
            KISMockFailureReason.BALANCE_POSITIONS_INVALID,
            "KIS mock balance positions are invalid",
        )
    parsed: list[tuple[str, int, int]] = []
    for row in value:
        if not isinstance(row, dict):
            raise KISMockProjectionError(
                KISMockFailureReason.BALANCE_POSITIONS_INVALID,
                "KIS mock balance position is invalid",
            )
        symbol = row.get("pdno")
        if not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None:
            raise KISMockProjectionError(
                KISMockFailureReason.BALANCE_POSITIONS_INVALID,
                "KIS mock balance symbol is invalid",
            )
        parsed.append(
            (
                symbol,
                _nonnegative(
                    row.get("hldg_qty"),
                    "holding quantity",
                    reason=KISMockFailureReason.BALANCE_POSITIONS_INVALID,
                ),
                _nonnegative(
                    row.get("evlu_amt"),
                    "position market value",
                    reason=KISMockFailureReason.BALANCE_POSITIONS_INVALID,
                ),
            )
        )
    if len({row[0] for row in parsed}) != len(parsed):
        raise KISMockProjectionError(
            KISMockFailureReason.BALANCE_POSITIONS_INVALID,
            "KIS mock balance contains duplicate symbols",
        )
    return tuple(sorted(parsed))


def _single_object(
    value: object,
    label: str,
    *,
    reason: KISMockFailureReason | None = None,
) -> dict[str, Any]:
    if isinstance(value, list):
        if len(value) != 1 or not isinstance(value[0], dict):
            _raise_projection_error(label, reason)
        return value[0]
    if not isinstance(value, dict):
        _raise_projection_error(label, reason)
    return value


def _nonnegative(
    value: object,
    field: str,
    *,
    reason: KISMockFailureReason | None = None,
) -> int:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        _raise_numeric_error(field, reason)
    normalized = str(value).replace(",", "").strip()
    if not normalized.isdigit():
        _raise_numeric_error(field, reason)
    parsed = int(normalized)
    if parsed > _MAX_BIGINT:
        _raise_numeric_error(field, reason)
    return parsed


def _raise_projection_error(
    label: str,
    reason: KISMockFailureReason | None,
) -> NoReturn:
    message = f"KIS mock {label} response is invalid"
    if reason is None:
        raise ValueError(message)
    raise KISMockProjectionError(reason, message)


def _raise_numeric_error(
    field: str,
    reason: KISMockFailureReason | None,
) -> NoReturn:
    message = f"KIS mock {field} is invalid"
    if reason is None:
        raise ValueError(message)
    raise KISMockProjectionError(reason, message)


def _now_text() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
