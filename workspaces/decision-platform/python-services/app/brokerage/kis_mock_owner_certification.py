"""One exact owner-bound KIS_MOCK test order for account certification."""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.brokerage.kis_mock_online_client import KISBrokerageCallBudget, KISMockBrokerageHttpClient
from app.brokerage.kis_mock_online_runtime import (
    KISMockExecutionReader,
    KISMockOnlineBalanceReader,
    KISMockProjectionError,
)
from app.brokerage.kis_mock_order_gateway import (
    KISMockOrderGateway,
    MockOrderIntent,
    MockOrderRecoveryError,
    MockOrderRejected,
)
from app.brokerage.kis_mock_certification_gate import (
    CertificationWindowClosed,
    require_certification_window,
)
from app.brokerage.mock_order_reference_store import (
    EncryptedRedisOrderReferenceStore,
    MockProviderOrderReference,
    MockOrderReferenceUnavailable,
)
from app.data._shared.canonical_json import canonical_json_bytes
from app.data.kis.accounting import (
    CollectionRunRecorder,
    CollectionRunStatus,
    FailureCode,
    LogicalOperation,
    PhysicalChannel,
)
from app.data.kis.http_client import CURRENT_PRICE_PATH, KISHttpClient
from app.data.kis.settings import KISSettings

_CERTIFICATION_ID = re.compile(r"^cert_[0-9a-f]{32}$")
_SYMBOL = "005930"
_QUANTITY = 1
_POST_CANCEL_SETTLEMENT_SECONDS = 5.0
_CERTIFICATION_TTL = timedelta(minutes=4)
_KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True, slots=True)
class OwnerMockCertificationResult:
    state: str
    certification_id: str
    receipt_sha256: str
    session_date: str
    quote_calls: int
    brokerage_calls: int
    token_calls: int
    failure_code: str


class OwnerMockCredentialCertifier:
    """Only the certified fixed order/cancel/reconcile sequence is available to the caller."""

    def __init__(
        self,
        *,
        account_id: str,
        certification_id: str,
        session_date: str,
        recovery: bool,
        quote_client: KISHttpClient,
        quote_accounting: CollectionRunRecorder,
        broker_client: KISMockBrokerageHttpClient,
        brokerage_budget: KISBrokerageCallBudget,
        gateway: KISMockOrderGateway,
        balance_reader: KISMockOnlineBalanceReader,
        execution_reader: KISMockExecutionReader,
        reference_store: EncryptedRedisOrderReferenceStore,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if _CERTIFICATION_ID.fullmatch(certification_id) is None:
            raise ValueError("KIS_MOCK_CERTIFICATION_ID_INVALID")
        parsed_session = date.fromisoformat(session_date)
        if parsed_session.isoformat() != session_date:
            raise ValueError("KIS_MOCK_CERTIFICATION_SESSION_INVALID")
        self._account_id = account_id
        self._certification_id = certification_id
        self._order_id = "ord_mock_" + certification_id.removeprefix("cert_")
        self._expected_session_date = session_date
        self._recovery = recovery
        self._quote_client = quote_client
        self._quote_accounting = quote_accounting
        self._broker_client = broker_client
        self._brokerage_budget = brokerage_budget
        self._gateway = gateway
        self._balance_reader = balance_reader
        self._execution_reader = execution_reader
        self._reference_store = reference_store
        self._now = now
        self._sleep = sleep
        self._submitted = False
        self._session_date = session_date
        self._current_session_date = ""
        self._quote_succeeded = False

    def run(self) -> OwnerMockCertificationResult:
        try:
            current_session = require_certification_window(self._now())
            if not self._recovery and current_session != self._expected_session_date:
                return self._result("FAILED", "MARKET_CLOSED")
            self._session_date = self._expected_session_date
            self._current_session_date = current_session
            expires_at = self._now() + _CERTIFICATION_TTL
            reference_state = self._reference_store.state(self._order_id, self._account_id)
            if self._recovery and reference_state is None:
                return self._result("RECOVERY_REQUIRED", "TEST_ORDER_UNCERTAIN")
            if reference_state is not None:
                return self._recover(reference_state)

            lower_limit, quote_calls = self._read_lower_limit(expires_at)
            if quote_calls["marketData"] != 1:
                return self._result("FAILED", "QUOTE_INVALID")
            before = self._balance_reader.probe_balance_source(self._account_id)
            before_digest = before.reconciliation_digest()
            buyable = self._balance_reader.buyable(
                self._account_id,
                _SYMBOL,
                lower_limit,
                order_division="00",
            )
            if (
                buyable is None
                or buyable.account_id != self._account_id
                or buyable.symbol != _SYMBOL
                or buyable.buyable_quantity < _QUANTITY
                or buyable.buyable_amount_krw < lower_limit
            ):
                return self._result("FAILED", "BUYABLE_UNAVAILABLE")

            brokerage_calls_before_submit = self._brokerage_budget.counts["brokerage"]
            try:
                receipt = self._gateway.submit_cash_order(
                    MockOrderIntent(
                        symbol=_SYMBOL,
                        side="BUY",
                        order_type="LIMIT",
                        quantity=_QUANTITY,
                        estimated_price=lower_limit,
                        order_division="00",
                        exchange_division="KRX",
                    ),
                    order_id=self._order_id,
                    account_id=self._account_id,
                )
            except MockOrderRecoveryError as error:
                if error.reason_code == "ORDER_REFERENCE_COMMIT_COMPENSATED":
                    return self._result("FAILED", "PROVIDER_FAILED")
                self._submitted = True
                return self._result("RECOVERY_REQUIRED", "TEST_ORDER_UNCERTAIN")
            except MockOrderRejected:
                return self._result("FAILED", "PROVIDER_FAILED")
            except Exception:
                self._submitted = (
                    self._brokerage_budget.counts["brokerage"] > brokerage_calls_before_submit
                    or self._reference_store.state(self._order_id, self._account_id) is not None
                )
                raise
            self._submitted = True
            if not receipt.accepted:
                return self._result("RECOVERY_REQUIRED", "TEST_ORDER_UNCERTAIN")
            cancellation = self._gateway.cancel_cash_order(
                order_id=self._order_id,
                account_id=self._account_id,
            )
            if cancellation.status != "CANCELLED":
                return self._result("RECOVERY_REQUIRED", "TEST_ORDER_UNCERTAIN")
            self._sleep(_POST_CANCEL_SETTLEMENT_SECONDS)
            reference = self._required_reference()
            try:
                self._execution_reader.verify_cancelled_unfilled(
                    reference=reference,
                    start=date.fromisoformat(self._session_date),
                    end=date.fromisoformat(self._session_date),
                    recent=True,
                )
            except KISMockProjectionError as error:
                if error.reason_code != "EXECUTION_FILL_DETECTED":
                    raise
                try:
                    self._execution_reader.require_no_open_order(
                        reference=reference,
                        start=date.fromisoformat(self._session_date),
                        end=date.fromisoformat(self._session_date),
                        recent=True,
                    )
                except KISMockProjectionError:
                    return self._result("RECOVERY_REQUIRED", "TEST_ORDER_UNCERTAIN")
                return self._result("RECOVERY_REQUIRED", "EXECUTION_FILLED")
            self._execution_reader.require_no_open_order(
                reference=reference,
                start=date.fromisoformat(self._session_date),
                end=date.fromisoformat(self._session_date),
                recent=True,
            )
            after = self._balance_reader.probe_balance_source(self._account_id)
            after_digest = after.reconciliation_digest()
            if not hmac.compare_digest(before_digest, after_digest):
                return self._result("RECOVERY_REQUIRED", "BALANCE_CHANGED")
            quote_counts = self._quote_counts()
            calls = self._brokerage_budget.counts
            token_calls = quote_counts["tokenP"] + calls["tokenP"]
            if quote_counts["marketData"] != 1 or calls["brokerage"] != 7 or token_calls > 1:
                return self._result("RECOVERY_REQUIRED", "TEST_ORDER_UNCERTAIN")
            receipt_sha256 = _receipt_sha256(
                self._account_id,
                self._certification_id,
                self._session_date,
                lower_limit,
                before_digest,
                after_digest,
                quote_counts["marketData"],
                calls["brokerage"],
                token_calls,
            )
            return self._result("PASS", "", receipt_sha256=receipt_sha256)
        except CertificationWindowClosed:
            if self._recovery:
                return self._result("RECOVERY_REQUIRED", "TEST_ORDER_UNCERTAIN")
            return self._result("FAILED", "MARKET_CLOSED")
        except KISMockProjectionError as error:
            if error.reason_code == "EXECUTION_FILL_DETECTED":
                return self._result("RECOVERY_REQUIRED", "EXECUTION_FILLED")
            return self._result(
                "RECOVERY_REQUIRED" if self._submitted or self._recovery else "FAILED",
                "TEST_ORDER_UNCERTAIN" if self._submitted or self._recovery else "PROVIDER_FAILED",
            )
        except Exception:
            return self._result(
                "RECOVERY_REQUIRED" if self._submitted or self._recovery else "FAILED",
                "TEST_ORDER_UNCERTAIN" if self._submitted or self._recovery else "PROVIDER_FAILED",
            )

    def _recover(self, reference_state: str | None) -> OwnerMockCertificationResult:
        if reference_state != "COMMITTED":
            # A durable PENDING marker or a missing reference after an ambiguous RPC is never
            # interpreted as permission to submit another test order for this attempt ID.
            return self._result("RECOVERY_REQUIRED", "TEST_ORDER_UNCERTAIN")
        reference = self._required_reference()
        recent = self._session_date == self._current_session_date
        before = self._balance_reader.probe_balance_source(self._account_id)
        before_digest = before.reconciliation_digest()
        window_start = date.fromisoformat(self._session_date)
        snapshot = self._execution_reader.read_optional(
            reference=reference,
            start=window_start,
            end=window_start,
            recent=recent,
        )
        if snapshot is None:
            self._execution_reader.require_no_open_order(
                reference=reference,
                start=window_start,
                end=window_start,
                recent=recent,
            )
            return self._result("RECOVERY_REQUIRED", "TEST_ORDER_UNCERTAIN")
        if snapshot.cumulative_quantity > 0 or snapshot.rejected:
            return self._result("RECOVERY_REQUIRED", "EXECUTION_FILLED")
        if snapshot.leaves_quantity > 0 and not snapshot.cancelled:
            # This explicit recovery click cancels only the exact order whose encrypted reference
            # is bound to this owner's certification attempt. No provider write is retried.
            cancellation = self._gateway.cancel_cash_order(
                order_id=self._order_id,
                account_id=self._account_id,
            )
            if cancellation.status != "CANCELLED":
                return self._result("RECOVERY_REQUIRED", "TEST_ORDER_UNCERTAIN")
            self._sleep(_POST_CANCEL_SETTLEMENT_SECONDS)
            self._execution_reader.verify_cancelled_unfilled(
                reference=reference,
                start=window_start,
                end=window_start,
                recent=recent,
            )
        elif not snapshot.cancelled or snapshot.leaves_quantity != 0:
            return self._result("RECOVERY_REQUIRED", "TEST_ORDER_UNCERTAIN")
        self._execution_reader.require_no_open_order(
            reference=reference,
            start=window_start,
            end=window_start,
            recent=recent,
        )
        after = self._balance_reader.probe_balance_source(self._account_id)
        after_digest = after.reconciliation_digest()
        if not hmac.compare_digest(before_digest, after_digest):
            return self._result("RECOVERY_REQUIRED", "BALANCE_CHANGED")
        # A previous test order has now been positively reconciled. Ask for a fresh explicit
        # certification click, which receives a new attempt ID and cannot duplicate this order.
        return self._result("FAILED", "TEST_ORDER_RECOVERED")

    def _read_lower_limit(self, expires_at: datetime) -> tuple[int, dict[str, int]]:
        recorder = self._quote_accounting
        logical_token = recorder.start_logical(LogicalOperation.CURRENT_PRICE)
        try:
            self._require_before(expires_at)
            response = self._quote_client.request(
                "GET",
                CURRENT_PRICE_PATH,
                KISSettings(kis_mode="mock", kis_offline=False).current_price_tr_id,
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": _SYMBOL},
            )
            output = response.get("output")
            if not isinstance(output, Mapping):
                raise ValueError("KIS_MOCK_PRICE_RESPONSE_INVALID")
            lower_limit = _positive_int(output.get("stck_llam"))
            if lower_limit % _krx_tick(lower_limit) != 0:
                raise ValueError("KIS_MOCK_LOWER_LIMIT_INVALID")
            recorder.succeed_logical(logical_token)
            self._quote_succeeded = True
        except Exception:
            try:
                recorder.fail_logical(logical_token, FailureCode.UNKNOWN_INTERNAL)
            except Exception:
                pass
            raise
        counts = self._quote_counts()
        if counts["marketData"] != 1 or counts["tokenP"] not in {0, 1}:
            raise ValueError("KIS_MOCK_PRICE_CALL_CAP_INVALID")
        return lower_limit, counts

    def _quote_counts(self) -> dict[str, int]:
        summary = self._quote_accounting.snapshot(
            completed_at=max(self._now(), datetime.now(UTC)),
            status=CollectionRunStatus.SUCCESS
            if self._quote_succeeded
            else CollectionRunStatus.FAILED,
        )
        counts = {item.channel.value: item.attempts for item in summary.physical_attempts}
        return {"marketData": counts.get("marketData", 0), "tokenP": counts.get("tokenP", 0)}

    def _required_reference(self) -> MockProviderOrderReference:
        reference = self._reference_store.get(self._order_id, self._account_id)
        if reference is None:
            raise MockOrderReferenceUnavailable("KIS_MOCK_CERTIFICATION_REFERENCE_UNAVAILABLE")
        return reference

    def _require_before(self, expires_at: datetime) -> None:
        if self._now() >= expires_at:
            raise TimeoutError("KIS_MOCK_CERTIFICATION_DEADLINE_EXPIRED")

    def _result(
        self,
        state: str,
        failure_code: str,
        *,
        receipt_sha256: str = "",
    ) -> OwnerMockCertificationResult:
        calls = self._brokerage_budget.counts
        quote_counts = self._quote_counts()
        return OwnerMockCertificationResult(
            state=state,
            certification_id=self._certification_id,
            receipt_sha256=receipt_sha256,
            session_date=self._session_date or self._now().astimezone(_KST).date().isoformat(),
            quote_calls=quote_counts["marketData"],
            brokerage_calls=calls["brokerage"],
            token_calls=quote_counts["tokenP"] + calls["tokenP"],
            failure_code=failure_code,
        )


def build_quote_accounting() -> CollectionRunRecorder:
    return CollectionRunRecorder(
        run_id=uuid4(),
        started_at=datetime.now(UTC),
        logical_caps={LogicalOperation.CURRENT_PRICE: 1},
        physical_caps={PhysicalChannel.MARKET_DATA: 1, PhysicalChannel.TOKEN_P: 1},
    )


def _receipt_sha256(
    account_id: str,
    certification_id: str,
    session_date: str,
    lower_limit: int,
    before_digest: str,
    after_digest: str,
    quote_calls: int,
    brokerage_calls: int,
    token_calls: int,
) -> str:
    proof = canonical_json_bytes(
        {
            "accountId": account_id,
            "balanceAfterDigest": after_digest,
            "balanceBeforeDigest": before_digest,
            "brokerageCalls": brokerage_calls,
            "certificationId": certification_id,
            "lowerLimitKrw": lower_limit,
            "quantity": _QUANTITY,
            "quoteCalls": quote_calls,
            "sessionDate": session_date,
            "side": "BUY",
            "symbol": _SYMBOL,
            "tokenCalls": token_calls,
            "version": 1,
        }
    )
    return hashlib.sha256(proof).hexdigest()


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("KIS_MOCK_PRICE_RESPONSE_INVALID")
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("KIS_MOCK_PRICE_RESPONSE_INVALID")
    return parsed


def _krx_tick(price: int) -> int:
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000
