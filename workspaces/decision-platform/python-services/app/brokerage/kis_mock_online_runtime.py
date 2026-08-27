"""KIS_MOCK online client의 sanitized balance/buyable/execution application adapter."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal, NoReturn

from app.brokerage.kis_mock_online_client import (
    BALANCE_PATH,
    BUYABLE_PATH,
    EXECUTIONS_PATH,
    MOCK_BALANCE_TR_ID,
    MOCK_BUYABLE_TR_ID,
    MOCK_EXECUTIONS_ARCHIVE_TR_ID,
    MOCK_EXECUTIONS_RECENT_TR_ID,
    KISMockBrokerageHttpClient,
    KISMockFailureReason,
)
from app.brokerage.mock_order_reference_store import MockProviderOrderReference
from app.generated import brokerage_pb2

_SYMBOL = re.compile(r"^[0-9]{6}$")
_ORDER_DIVISION = re.compile(r"^[0-9]{2}$")
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
class KISMockExecutionSourceProbe:
    """exact probe용 체결조회 source-shape이며 특정 주문 row 출현을 보장하지 않는다."""

    provider_exec_ref_hash: str | None
    rows_seen: int
    matched: bool


@dataclass(frozen=True, slots=True)
class KISMockBalanceSourceProbe:
    """연결성 진단용 sanitized source shape이며 완전한 risk balance를 표현하지 않는다."""

    account_id: str
    cash_krw: int
    portfolio_equity_krw: int
    positions: tuple[tuple[str, int, int], ...]
    positions_complete: bool

    def reconciliation_digest(self) -> str:
        """완전한 sanitized balance projection만 pre/post 비교용 digest로 만든다."""

        if not self.positions_complete:
            raise KISMockProjectionError(
                KISMockFailureReason.BALANCE_PAGINATION_REQUIRED,
                "KIS mock balance reconciliation requires a complete page",
            )
        payload = {
            "accountId": self.account_id,
            "cashKrw": self.cash_krw,
            "portfolioEquityKrw": self.portfolio_equity_krw,
            "positions": self.positions,
            "schemaVersion": 1,
        }
        return hashlib.sha256(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class KISMockOpenOrderReconciliation:
    """raw 주문번호 없이 exact 미체결 조회가 비었음을 증명하는 process-local 결과다."""

    provider_exec_ref_hash: str
    rows_seen: int


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
        """exact-approved 진단에서 cash/equity/position source shape만 bounded 검증한다.

        provider가 continuation cursor를 돌려줘도 이 probe 결과는 publish/risk 근거가 아니므로
        첫 page shape만 검증하고 partial flag를 남긴다. 완전한 owner-facing balance는 여전히
        trusted enrichment와 별도 pagination 처리가 없으면 fail-closed다.
        """
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
        positions_complete = not (
            _has_continuation_cursor(payload.get("ctx_area_fk100"))
            or _has_continuation_cursor(payload.get("ctx_area_nk100"))
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
            positions_complete=positions_complete,
        )

    def buyable(
        self,
        account_id: str,
        symbol: str,
        estimated_price_krw: int,
        order_division: str = "00",
    ) -> brokerage_pb2.GetMockBuyableResponse | None:
        """주문가능 조회는 실제 주문과 같은 KRX 주문구분으로만 검증한다."""
        if (
            _SYMBOL.fullmatch(symbol) is None
            or estimated_price_krw <= 0
            or _ORDER_DIVISION.fullmatch(order_division) is None
        ):
            raise ValueError("KIS mock buyable input is invalid")
        payload = self._client.request(
            "GET",
            BUYABLE_PATH,
            MOCK_BUYABLE_TR_ID,
            params={
                "PDNO": symbol,
                "ORD_UNPR": str(estimated_price_krw),
                "ORD_DVSN": order_division,
                "CMA_EVLU_AMT_ICLD_YN": "N",
                "OVRS_ICLD_YN": "N",
            },
        )
        output = _single_object(payload.get("output"), "buyable")
        return brokerage_pb2.GetMockBuyableResponse(
            account_id=account_id,
            symbol=symbol,
            estimated_price_krw=estimated_price_krw,
            # max_buy_*는 미수 가능 한도를 포함할 수 있으므로 무미수 원칙의 근거가 아니다.
            buyable_quantity=_nonnegative(output.get("nrcvb_buy_qty"), "buyable quantity"),
            buyable_amount_krw=_nonnegative(output.get("nrcvb_buy_amt"), "buyable amount"),
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
        """대사용 strict reader는 지정 주문 row가 정확히 하나 보일 때만 snapshot을 만든다."""
        payload = self._request_execution_page(
            reference=reference,
            start=start,
            end=end,
            recent=recent,
        )
        rows = _strict_execution_rows(payload, require_nonempty=True)
        return _execution_snapshot_from_rows(rows, reference)

    def read_optional(
        self,
        *,
        reference: MockProviderOrderReference,
        start: date,
        end: date,
        recent: bool,
    ) -> MockExecutionSnapshot | None:
        """Read at most one exact snapshot with one provider page and no probe/read duplication."""

        payload = self._request_execution_page(
            reference=reference,
            start=start,
            end=end,
            recent=recent,
        )
        rows = _execution_source_probe_rows(payload)
        matches = [row for row in rows if _execution_order_no(row) == reference.provider_order_no]
        if len(matches) > 1:
            raise ValueError("KIS mock execution order match is not unique")
        if not matches:
            return None
        return _execution_snapshot_from_rows(matches, reference)

    def probe_execution_source(
        self,
        *,
        reference: MockProviderOrderReference,
        start: date,
        end: date,
        recent: bool,
    ) -> KISMockExecutionSourceProbe:
        """exact-approved FULL probe에서는 endpoint shape와 bounded page만 검증한다.

        낮은 지정가 주문을 즉시 전량취소하면 체결 row가 없거나 provider 조회 반영이 늦을 수 있다.
        이 진단은 publish/reconciliation 근거가 아니므로 row 부재는 성공적인 source-shape로 남기고,
        실제 대사는 기존 ``read``가 계속 fail-closed로 처리한다.
        """
        payload = self._request_execution_page(
            reference=reference,
            start=start,
            end=end,
            recent=recent,
        )
        rows = _execution_source_probe_rows(payload)
        matches = [row for row in rows if _execution_order_no(row) == reference.provider_order_no]
        if len(matches) > 1:
            raise ValueError("KIS mock execution order match is not unique")
        if not matches:
            return KISMockExecutionSourceProbe(
                provider_exec_ref_hash=None,
                rows_seen=len(rows),
                matched=False,
            )
        return KISMockExecutionSourceProbe(
            provider_exec_ref_hash=_execution_source_probe_hash(reference),
            rows_seen=len(rows),
            matched=True,
        )

    def verify_cancelled_unfilled(
        self,
        *,
        reference: MockProviderOrderReference,
        start: date,
        end: date,
        recent: bool,
    ) -> KISMockExecutionSourceProbe:
        """전체 체결조회에서 exact 주문의 체결·부분체결 또는 미취소 잔량을 거부한다."""

        try:
            payload = self._request_execution_page(
                reference=reference,
                start=start,
                end=end,
                recent=recent,
                ccld_dvsn="00",
            )
            rows = _execution_source_probe_rows(payload)
            matches = [
                row for row in rows if _execution_order_no(row) == reference.provider_order_no
            ]
            if len(matches) > 1:
                raise ValueError("KIS mock execution order match is not unique")
            if not matches:
                return KISMockExecutionSourceProbe(
                    provider_exec_ref_hash=None,
                    rows_seen=len(rows),
                    matched=False,
                )
            snapshot = _execution_snapshot_from_rows(matches, reference)
        except KISMockProjectionError:
            raise
        except ValueError:
            raise KISMockProjectionError(
                KISMockFailureReason.EXECUTION_RECONCILIATION_FAILED,
                "KIS mock execution reconciliation is invalid",
            ) from None
        if (
            snapshot.cumulative_quantity != 0
            or snapshot.leaves_quantity != 0
            or not snapshot.cancelled
        ):
            raise KISMockProjectionError(
                KISMockFailureReason.EXECUTION_FILL_DETECTED,
                "KIS mock final probe observed a fill or remaining quantity",
            )
        return KISMockExecutionSourceProbe(
            provider_exec_ref_hash=snapshot.provider_exec_ref_hash,
            rows_seen=len(rows),
            matched=True,
        )

    def require_no_open_order(
        self,
        *,
        reference: MockProviderOrderReference,
        start: date,
        end: date,
        recent: bool,
    ) -> KISMockOpenOrderReconciliation:
        """CCLD_DVSN=02와 exact 주문번호로 미체결 잔존·continuation을 모두 거부한다."""

        try:
            payload = self._request_execution_page(
                reference=reference,
                start=start,
                end=end,
                recent=recent,
                ccld_dvsn="02",
            )
            rows = _execution_source_probe_rows(payload)
        except ValueError:
            raise KISMockProjectionError(
                KISMockFailureReason.OPEN_ORDER_RECONCILIATION_FAILED,
                "KIS mock open-order reconciliation is invalid",
            ) from None
        if rows:
            raise KISMockProjectionError(
                KISMockFailureReason.OPEN_ORDER_RECONCILIATION_FAILED,
                "KIS mock order remains open after cancellation",
            )
        return KISMockOpenOrderReconciliation(
            provider_exec_ref_hash=_execution_source_probe_hash(reference),
            rows_seen=0,
        )

    def _request_execution_page(
        self,
        *,
        reference: MockProviderOrderReference,
        start: date,
        end: date,
        recent: bool,
        ccld_dvsn: Literal["00", "02"] = "00",
    ) -> dict[str, Any]:
        if start > end or (end - start).days > 31:
            raise ValueError("KIS mock execution window is invalid")
        tr_id = MOCK_EXECUTIONS_RECENT_TR_ID if recent else MOCK_EXECUTIONS_ARCHIVE_TR_ID
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
                "CCLD_DVSN": ccld_dvsn,
                "ORD_GNO_BRNO": reference.provider_org_no,
                "ODNO": reference.provider_order_no,
                "INQR_DVSN_3": "00",
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
                "EXCG_ID_DVSN_CD": reference.exchange_division,
            },
        )
        return payload


def _strict_execution_rows(
    payload: dict[str, Any],
    *,
    require_nonempty: bool,
) -> list[dict[str, Any]]:
    if _has_continuation_cursor(payload.get("ctx_area_fk100")) or _has_continuation_cursor(
        payload.get("ctx_area_nk100")
    ):
        raise ValueError("KIS mock execution response requires another bounded page")
    rows = payload.get("output1")
    min_rows = 1 if require_nonempty else 0
    if not isinstance(rows, list) or not min_rows <= len(rows) <= _MAX_MOCK_EXECUTION_ROWS:
        raise ValueError("KIS mock execution response is incomplete")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("KIS mock execution response is incomplete")
    return rows


def _execution_source_probe_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """exact probe 전용 parser: no-data 표현은 snapshot 없이 source-shape로만 닫는다."""

    if _has_continuation_cursor(payload.get("ctx_area_fk100")) or _has_continuation_cursor(
        payload.get("ctx_area_nk100")
    ):
        raise ValueError("KIS mock execution response requires another bounded page")
    rows = payload.get("output1")
    if rows is None or rows == "" or rows == {}:
        return []
    if isinstance(rows, dict):
        return [rows]
    if not isinstance(rows, list) or len(rows) > _MAX_MOCK_EXECUTION_ROWS:
        raise ValueError("KIS mock execution response is incomplete")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("KIS mock execution response is incomplete")
    return rows


def _execution_source_probe_hash(reference: MockProviderOrderReference) -> str:
    """raw provider order number는 probe 결과에도 싣지 않고 one-way digest만 남긴다."""

    identity = f"kis-mock-execution-source-probe/v1\0{reference.provider_order_no}"
    return hashlib.sha256(identity.encode()).hexdigest()


def _has_continuation_cursor(value: object) -> bool:
    """KIS가 빈 cursor를 공백 문자열로 보낼 수 있어 trim 후 continuation 여부를 판단한다."""

    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return bool(value)


def _execution_order_no(row: dict[str, Any]) -> object:
    """KIS output row의 주문번호 key는 문서/환경에 따라 lower/upper case가 섞일 수 있다."""

    if "odno" in row:
        return row.get("odno")
    return row.get("ODNO")


def _execution_snapshot_from_rows(
    rows: list[dict[str, Any]],
    reference: MockProviderOrderReference,
) -> MockExecutionSnapshot:
    matches = [row for row in rows if _execution_order_no(row) == reference.provider_order_no]
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
    observed_at = (
        datetime.strptime(
            f"{order_date}{order_time}",
            "%Y%m%d%H%M%S",
        )
        .replace(tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Seoul"))
        .astimezone(UTC)
    )
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
