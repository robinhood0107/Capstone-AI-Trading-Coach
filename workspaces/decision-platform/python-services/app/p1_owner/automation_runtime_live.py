"""Spring bridge와 기존 KIS reader를 재사용하는 persistent automation live port."""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

import httpx
from pydantic import SecretStr

from app.brokerage.brokerage_grpc_server import BrokerageGrpcServerSettings
from app.brokerage.kis_mock_online_client import KISBrokerageCallBudget, KISMockBrokerageHttpClient
from app.brokerage.kis_mock_online_runtime import KISMockExecutionReader, KISMockOnlineBalanceReader
from app.brokerage.mock_order_reference_store import (
    EncryptedRedisOrderReferenceStore,
    MockProviderOrderReference,
)
from app.data._shared.canonical_json import canonical_json_bytes
from app.data.kis._credential_transport import _build_redis_client
from app.data.kis.http_client import CURRENT_PRICE_PATH, KISHttpClient
from app.data.kis.settings import KISSettings
from app.p1_owner.automation import (
    AccountLineageSnapshot,
    AutomationError,
    AutomationInputs,
    AutomationRun,
    NewsVerdict,
    OrderReservation,
    Quote,
    ReconcileSnapshot,
    ReconcileOutcome,
    SubmitOutcome,
    _limit_price,
)
from app.p1_owner.vertex_corpus_evidence import (
    CorpusDocumentSource,
    EmptyCorpusDocumentSource,
    build_public_evidence,
)
from app.p1_owner.vertex_transport import VertexAiVetoTransport, VertexTransportSettings
from app.p1_owner.automation_runtime import (
    AccountLineageAdvance,
    AutomationRuntimeError,
    RuntimeClaim,
    inputs_from_state,
)
from app.p1_owner.vertex_veto import (
    MODEL_ID,
    PROMPT_VERSION,
    REQUEST_CONTRACT_ID,
    VertexBudgetExhausted,
    VertexTransportResult,
    VertexVetoTransport,
    evaluate_vertex_buy_veto,
)

_KST = ZoneInfo("Asia/Seoul")
_SECRET = re.compile(r"^[A-Za-z0-9._~:-]{32,256}$")
_ORDER_ID = re.compile(r"^ord_mock_[0-9a-f]{32}$")
_DECISION_ID = re.compile(r"^dec_[0-9a-f]{32}$")


class SpringAutomationBridgePort(Protocol):
    def command(
        self,
        operation: str,
        user_id: str,
        payload: dict[str, object],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]: ...

    def close(self) -> None: ...


class QuoteSourcePort(Protocol):
    def quote(self, symbol: str) -> Quote: ...

    def close(self) -> None: ...


class ExecutionSourcePort(Protocol):
    def balance(self, account_id: str) -> dict[str, object]: ...

    def read(
        self, order_id: str, account_id: str, session_date: date
    ) -> ReconcileOutcome | ReconcileSnapshot: ...

    def require_closed(self, order_id: str, account_id: str, session_date: date) -> bool: ...

    def close(self) -> None: ...


class SpringAutomationBridgeClient:
    """numeric loopback와 per-install secret에 고정된 retry-0 internal Spring client다."""

    def __init__(
        self,
        shared_secret: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if _SECRET.fullmatch(shared_secret) is None:
            raise AutomationRuntimeError("AUTOMATION_BRIDGE_SECRET_INVALID")
        self._secret = shared_secret
        self._client = httpx.Client(
            base_url="http://127.0.0.1:8080",
            transport=transport or httpx.HTTPTransport(retries=0),
            timeout=2.0,
            follow_redirects=False,
            trust_env=False,
        )

    def command(
        self,
        operation: str,
        user_id: str,
        payload: dict[str, object],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "idempotencyKey": idempotency_key,
            "operation": operation,
            "payload": payload,
            "userId": user_id,
        }
        response = self._client.post(
            "/internal/automation-runtime/command",
            content=canonical_json_bytes(body),
            headers={
                "Content-Type": "application/json",
                "X-Automation-Runtime-Auth": self._secret,
            },
        )
        if response.status_code != 200 or len(response.content) > 64 * 1024:
            raise AutomationRuntimeError("AUTOMATION_BRIDGE_FAILED")
        try:
            parsed = response.json()
        except json.JSONDecodeError as error:
            raise AutomationRuntimeError("AUTOMATION_BRIDGE_RESPONSE_INVALID") from error
        if (
            not isinstance(parsed, dict)
            or parsed.get("status") != "OK"
            or not isinstance(parsed.get("data"), dict)
        ):
            raise AutomationRuntimeError("AUTOMATION_BRIDGE_RESPONSE_INVALID")
        return cast(dict[str, Any], parsed["data"])

    def close(self) -> None:
        self._client.close()


class KisAutomationQuoteSource:
    """현재가 한 번에서 price/상한가/하한가만 즉시 축약한다."""

    def __init__(self) -> None:
        self._settings = KISSettings(kis_mode="mock", kis_offline=False, kis_retry_attempts=1)
        self._client = KISHttpClient(self._settings)

    def quote(self, symbol: str) -> Quote:
        payload = self._client.request(
            "GET",
            CURRENT_PRICE_PATH,
            self._settings.current_price_tr_id,
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
        )
        output = payload.get("output")
        if not isinstance(output, dict):
            raise AutomationRuntimeError("AUTOMATION_QUOTE_INVALID")
        try:
            price = _positive_int(output.get("stck_prpr"))
            lower = _positive_int(output.get("stck_llam"))
            upper = _positive_int(output.get("stck_mxpr"))
        except ValueError as error:
            raise AutomationRuntimeError("AUTOMATION_QUOTE_INVALID") from error
        if not lower <= price <= upper:
            raise AutomationRuntimeError("AUTOMATION_QUOTE_INVALID")
        return Quote(symbol, price, lower, upper, fresh=True, is_etf_etn=False)

    def close(self) -> None:
        self._client.close()


class KisAutomationExecutionSource:
    """Redis ciphertext reference와 공식 체결조회만 사용하며 row가 불명확하면 UNRESOLVED다."""

    def __init__(self) -> None:
        settings = BrokerageGrpcServerSettings.from_env()
        self._redis = _build_redis_client()
        self._references = EncryptedRedisOrderReferenceStore(
            self._redis,
            encryption_key=SecretStr(settings.reference_key.get_secret_value()),
            ttl_seconds=settings.reference_ttl_seconds,
        )
        self._client = KISMockBrokerageHttpClient(
            settings=KISSettings(kis_mode="mock", kis_offline=False, kis_retry_attempts=1),
            budget=KISBrokerageCallBudget(token_p_cap=1, brokerage_cap=16),
        )
        self._reader = KISMockExecutionReader(self._client)
        self._balance_reader = KISMockOnlineBalanceReader(self._client)

    def balance(self, account_id: str) -> dict[str, object]:
        source = self._balance_reader.probe_balance_source(account_id)
        if not source.positions_complete:
            raise AutomationRuntimeError("AUTOMATION_BALANCE_PAGINATION_REQUIRED")
        # riskComplete는 여기서 정하지 않는다. 보유 종목 분류가 전부 확인됐는지는 durable state의
        # 카탈로그를 봐야 알 수 있으므로 호출자(inputs)가 판정한다.
        return {
            "accountId": source.account_id,
            "cashKrw": source.cash_krw,
            "marginRequirementKrw": source.margin_requirement_krw,
            "portfolioEquityKrw": source.portfolio_equity_krw,
            "positionsComplete": source.positions_complete,
            "positions": [
                {"marketValueKrw": market_value, "quantity": quantity, "symbol": symbol}
                for symbol, quantity, market_value in source.positions
            ],
        }

    def read(self, order_id: str, account_id: str, session_date: date) -> ReconcileSnapshot:
        reference = self._reference(order_id, account_id)
        try:
            snapshot = self._reader.read_optional(
                reference=reference,
                start=session_date,
                end=session_date,
                recent=True,
            )
        except ValueError:
            snapshot = None
        if snapshot is None:
            return ReconcileSnapshot(False, 0, reference.quantity, None)
        return ReconcileSnapshot(
            resolved=True,
            cumulative_quantity=snapshot.cumulative_quantity,
            leaves_quantity=snapshot.leaves_quantity,
            average_fill_price_krw=snapshot.average_fill_price_krw,
            cancelled=snapshot.cancelled,
            rejected=snapshot.rejected,
            provider_exec_ref_hash=snapshot.provider_exec_ref_hash,
        )

    def require_closed(self, order_id: str, account_id: str, session_date: date) -> bool:
        reference = self._reference(order_id, account_id)
        self._reader.require_no_open_order(
            reference=reference,
            start=session_date,
            end=session_date,
            recent=True,
        )
        return True

    def close(self) -> None:
        self._client.close()
        self._redis.close()

    def _reference(self, order_id: str, account_id: str) -> MockProviderOrderReference:
        reference = self._references.get(order_id, account_id)
        if reference is None:
            raise AutomationRuntimeError("AUTOMATION_ORDER_REFERENCE_UNAVAILABLE")
        return reference


class FailClosedVertexVetoTransport:
    """별도 production Vertex transport가 주입되지 않으면 provider 호출 전 ABSTAIN으로 닫는다."""

    physical_calls = 0

    def invoke(self, *, system_prompt: str, request_bytes: bytes) -> VertexTransportResult:
        del system_prompt, request_bytes
        raise VertexBudgetExhausted("AUTOMATION_VERTEX_TRANSPORT_NOT_CONFIGURED")


class LiveAutomationPort:
    """engine port를 Spring decision/brokerage, KIS quote/execution, Vertex veto에 결속한다."""

    def __init__(
        self,
        claim: RuntimeClaim,
        state: dict[str, Any],
        bridge: SpringAutomationBridgePort,
        quote_source: QuoteSourcePort,
        execution_source: ExecutionSourcePort,
        vertex_transport: VertexVetoTransport,
        corpus_source: CorpusDocumentSource | None = None,
    ) -> None:
        self._claim = claim
        self._corpus_source: CorpusDocumentSource = corpus_source or EmptyCorpusDocumentSource()
        self._bridge = bridge
        self._quote_source = quote_source
        self._execution_source = execution_source
        self._vertex_transport = vertex_transport
        self._cached_quotes: dict[str, Quote] = {}
        self.decision_id = (
            str(state.get("decisionId")) if isinstance(state.get("decisionId"), str) else None
        )
        reservation = state.get("reservation")
        self.order_id = (
            str(reservation.get("orderId"))
            if isinstance(reservation, dict) and isinstance(reservation.get("orderId"), str)
            else None
        )
        self.provider_order_ref_hash = (
            str(reservation.get("providerOrderRefHash"))
            if isinstance(reservation, dict)
            and isinstance(reservation.get("providerOrderRefHash"), str)
            else None
        )
        self.physical_calls = int(state.get("providerCallCount", 0))
        self.physical_submit_calls = int(state.get("logicalSubmitCount", 0))
        self.quote_calls = 0
        self.vertex_calls = int(state.get("vertexCallCount", 0))
        self.submit_calls = int(state.get("logicalSubmitCount", 0))
        self.reconcile_calls = 0
        self.cancel_calls = 0
        expected = state.get("expectedAccountProjection", state.get("baselineAccountProjection"))
        self._expected_projection: dict[str, Any] | None = (
            dict(expected) if isinstance(expected, dict) else None
        )

    def inputs(
        self,
        *,
        state: dict[str, Any],
        run: AutomationRun,
        now: datetime,
    ) -> AutomationInputs:
        risk_allow = True
        buyable_quantity = 1
        buyable_amount_krw = 9_223_372_036_854_775_807
        account_complete: bool | None = None
        account_digest_matches: bool | None = None
        runtime_state = dict(state)
        runtime_state["newsVetoProviderBound"] = not isinstance(
            self._vertex_transport, FailClosedVertexVetoTransport
        )
        if run.state == "RISK_CHECKING":
            reservation = run.reservation
            if reservation is None or reservation.intent is None:
                raise AutomationRuntimeError("AUTOMATION_EXACT_INTENT_MISSING")
            decision = self._bridge.command(
                "EVALUATE",
                self._claim.user_id,
                {
                    "principleId": self._claim.principle_id,
                    "portfolioSource": "KIS_MOCK",
                    "orderIntent": reservation.intent.projection(),
                },
                idempotency_key=_idempotency(self._claim.run_id, "decision"),
            )
            risk = decision.get("riskDecision")
            if not isinstance(risk, dict):
                raise AutomationRuntimeError("AUTOMATION_DECISION_INVALID")
            decision_id = decision.get("decisionId")
            if not isinstance(decision_id, str) or _DECISION_ID.fullmatch(decision_id) is None:
                raise AutomationRuntimeError("AUTOMATION_DECISION_INVALID")
            self.decision_id = decision_id
            risk_allow = risk.get("decision") == "ALLOW" and risk.get("canSubmitOrder") is True
        if run.state == "ORDER_SIZING":
            symbol = _required(run.selected_symbol)
            quote = run.selected_quote or self._quote(symbol)
            exact_limit_price = _limit_price(quote, _required_side(run.selected_side))
            self._require_capacity(1)
            balance = self._execution_source.balance(self._claim.account_id)
            self.physical_calls += 1
            if balance.get("accountId") != self._claim.account_id:
                raise AutomationRuntimeError("AUTOMATION_BALANCE_IDENTITY_MISMATCH")
            expected = state.get(
                "expectedAccountProjection", state.get("baselineAccountProjection")
            )
            if not isinstance(expected, dict):
                raise AutomationRuntimeError("AUTOMATION_BASELINE_PROJECTION_MISSING")
            account_complete = _risk_complete(balance, state)
            # 원칙 한도는 durable state가, 곱할 평가액은 live 잔고가 준다. 둘 다 있어야
            # 사이저가 사용자 원칙 안쪽에서 수량을 만든다.
            runtime_state["openPositionMarketValueKrw"] = _open_position_value(balance)
            try:
                expected_lineage = AccountLineageSnapshot.from_projection(expected)
                observed_lineage = AccountLineageSnapshot.from_projection(balance)
            except (AutomationError, TypeError, ValueError) as error:
                raise AutomationRuntimeError("AUTOMATION_ACCOUNT_LINEAGE_INVALID") from error
            account_digest_matches = expected_lineage.exact_match(observed_lineage)
            runtime_state["accountDigestMatches"] = account_digest_matches
            runtime_state["accountComplete"] = account_complete
            if account_complete and run.selected_side == "BUY":
                self._require_capacity(1)
                buyable = self._bridge.command(
                    "BUYABLE",
                    self._claim.user_id,
                    {
                        "accountId": self._claim.account_id,
                        "estimatedPrice": exact_limit_price,
                        "symbol": symbol,
                    },
                )
                self.physical_calls += 1
                if (
                    buyable.get("accountId") != self._claim.account_id
                    or buyable.get("symbol") != symbol
                    or buyable.get("estimatedPrice") != exact_limit_price
                ):
                    raise AutomationRuntimeError("AUTOMATION_BUYABLE_IDENTITY_MISMATCH")
                buyable_quantity = int(buyable.get("buyableQuantity", 0))
                buyable_amount_krw = int(buyable.get("buyableAmountKrw", 0))
        return inputs_from_state(
            runtime_state,
            risk_allow=risk_allow,
            buyable_quantity=buyable_quantity,
            buyable_amount_krw=buyable_amount_krw,
            account_complete=account_complete,
            account_digest_matches=account_digest_matches,
        )

    def quote(self, symbol: str) -> Quote:
        return self._quote(symbol)

    def vertex(self, symbol: str) -> NewsVerdict:
        self.vertex_calls += 1
        self._require_capacity(1)
        request = _vertex_request(
            self._claim.session_date,
            symbol,
            self._quote(symbol).price_krw,
            self._corpus_source,
        )
        before_calls = getattr(self._vertex_transport, "physical_calls", 0)
        result = json.loads(evaluate_vertex_buy_veto(request, transport=self._vertex_transport))
        after_calls = getattr(self._vertex_transport, "physical_calls", 0)
        if (
            isinstance(before_calls, int)
            and isinstance(after_calls, int)
            and after_calls >= before_calls
        ):
            self.physical_calls += after_calls - before_calls
        if result.get("status") != "AVAILABLE":
            return "ABSTAIN"
        verdict = result.get("verdict")
        return cast(NewsVerdict, verdict if verdict in {"VETO_BUY", "NO_VETO"} else "ABSTAIN")

    def submit(self, reservation: OrderReservation) -> SubmitOutcome:
        if self.decision_id is None or reservation.intent is None:
            raise AutomationRuntimeError("AUTOMATION_DECISION_MISSING")
        self.submit_calls += 1
        self._require_capacity(1)
        try:
            submitted = self._bridge.command(
                "SUBMIT",
                self._claim.user_id,
                {
                    "decisionId": self.decision_id,
                    "orderIntent": reservation.intent.projection(),
                    "userAcknowledgement": {"warningsAccepted": False},
                },
                idempotency_key=_idempotency(self._claim.run_id, "submit"),
            )
        except AutomationRuntimeError:
            self.physical_submit_calls = 1
            self.physical_calls += 1
            return "AMBIGUOUS"
        order_id = submitted.get("orderId")
        if not isinstance(order_id, str) or _ORDER_ID.fullmatch(order_id) is None:
            return "AMBIGUOUS"
        self.order_id = order_id
        self.physical_submit_calls = 1
        self.physical_calls += 1
        return "AMBIGUOUS" if submitted.get("status") == "PENDING_RECONCILIATION" else "UNFILLED"

    def reconcile(
        self, reservation: OrderReservation | None
    ) -> ReconcileOutcome | ReconcileSnapshot:
        del reservation
        self.reconcile_calls += 1
        if self.order_id is None:
            return "UNRESOLVED"
        self._require_capacity(1)
        self.physical_calls += 1
        return self._execution_source.read(
            self.order_id,
            self._claim.account_id,
            self._claim.session_date,
        )

    def account_lineage_advance(
        self,
        *,
        symbol: str,
        side: str,
        filled_quantity: int,
        average_fill_price_krw: int,
    ) -> AccountLineageAdvance | None:
        """확정된 자기 체결이면 전진할 계좌 투영을 만든다. 설명되지 않으면 None이다."""

        expected_projection = self._expected_projection
        if expected_projection is None or self.order_id is None:
            return None
        self._require_capacity(1)
        balance = self._execution_source.balance(self._claim.account_id)
        self.physical_calls += 1
        try:
            expected = AccountLineageSnapshot.from_projection(expected_projection)
            observed = AccountLineageSnapshot.from_projection(balance)
        except (AutomationError, TypeError, ValueError):
            return None
        if not expected.permits_fill(
            observed,
            symbol=symbol,
            side=cast(Any, side),
            filled_quantity=filled_quantity,
            average_fill_price_krw=average_fill_price_krw,
        ):
            # 델타가 자기 체결로 설명되지 않으면 전진시키지 않는다. 다음 tick이 드리프트로
            # 잡아 HALT하는 편이 잘못된 기대를 굳히는 것보다 안전하다.
            return None
        projection = observed.projection()
        projection["schemaVersion"] = "2"
        return AccountLineageAdvance(
            reason="BUY_FILL" if side == "BUY" else "SELL_FILL",
            projection=projection,
            digest=observed.digest,
            order_id=self.order_id,
            filled_quantity=filled_quantity,
            average_fill_price_krw=average_fill_price_krw,
        )

    def cancel(self, reservation: OrderReservation) -> bool:
        del reservation
        self.cancel_calls += 1
        if self.order_id is None:
            return False
        if self.physical_calls > 14:
            return False
        cancelled = self._bridge.command(
            "CANCEL",
            self._claim.user_id,
            {"orderId": self.order_id},
        )
        self.physical_calls += 1
        if cancelled.get("orderId") != self.order_id or cancelled.get("status") != "CANCELLED":
            return False
        self.physical_calls += 1
        return self._execution_source.require_closed(
            self.order_id,
            self._claim.account_id,
            self._claim.session_date,
        )

    def close(self) -> None:
        self._bridge.close()
        self._quote_source.close()
        self._execution_source.close()

    def _quote(self, symbol: str | None) -> Quote:
        selected = _required(symbol)
        if selected not in self._cached_quotes:
            if len(self._cached_quotes) >= 6:
                raise AutomationRuntimeError("AUTOMATION_QUOTE_CAP_EXHAUSTED")
            self._require_capacity(1)
            self._cached_quotes[selected] = self._quote_source.quote(selected)
            self.quote_calls += 1
            self.physical_calls += 1
        quote = self._cached_quotes[selected]
        if quote.symbol != selected:
            raise AutomationRuntimeError("AUTOMATION_QUOTE_IDENTITY_MISMATCH")
        return quote

    def _require_capacity(self, calls: int) -> None:
        if calls < 1 or self.physical_calls > 16 - calls:
            raise AutomationRuntimeError("AUTOMATION_PROVIDER_CALL_CAP_EXHAUSTED")


class LiveAutomationPortFactory:
    """supervisor process에서만 production dependencies를 지연 생성한다."""

    def build(self, claim: RuntimeClaim, state: dict[str, Any]) -> LiveAutomationPort:
        shared_secret = os.environ.get("AUTOMATION_RUNTIME_SHARED_SECRET", "").strip()
        return LiveAutomationPort(
            claim,
            state,
            SpringAutomationBridgeClient(shared_secret),
            KisAutomationQuoteSource(),
            KisAutomationExecutionSource(),
            _vertex_veto_transport(),
        )


def _balance_projection(balance: dict[str, Any]) -> dict[str, object]:
    positions = balance.get("positions")
    if not isinstance(positions, list):
        raise AutomationRuntimeError("AUTOMATION_BALANCE_INVALID")
    normalized_positions: list[dict[str, object]] = []
    for item in positions:
        if not isinstance(item, dict):
            raise AutomationRuntimeError("AUTOMATION_BALANCE_INVALID")
        normalized_positions.append(
            {
                "marketValueKrw": int(item["marketValueKrw"]),
                "quantity": int(item["quantity"]),
                "symbol": str(item["symbol"]),
            }
        )
    normalized_positions.sort(key=lambda item: cast(str, item["symbol"]))
    return {
        "accountId": str(balance["accountId"]),
        "cashKrw": int(balance["cashKrw"]),
        "portfolioEquityKrw": int(balance["portfolioEquityKrw"]),
        "positions": normalized_positions,
    }


def _order_intent_from_reservation(
    reservation: OrderReservation,
    strategy_id: str,
) -> dict[str, object]:
    if reservation.intent is None or reservation.intent.strategy_id != strategy_id:
        raise AutomationRuntimeError("AUTOMATION_EXACT_INTENT_MISSING")
    return reservation.intent.projection()


def _vertex_request(
    session_date: date,
    symbol: str,
    previous_close: int,
    corpus_source: CorpusDocumentSource,
) -> bytes:
    calendar = __import__("exchange_calendars").get_calendar("XKRX")
    current = calendar.date_to_session(
        __import__("pandas").Timestamp(session_date), direction="none"
    )
    previous = calendar.previous_session(current).date()
    return canonical_json_bytes(
        {
            "candidate": {
                "action": "NEW_BUY",
                "companyName": symbol,
                "previousClose": previous_close,
                "previousSessionDate": previous.isoformat(),
                "sessionDate": session_date.isoformat(),
                "symbol": symbol,
            },
            "contractId": REQUEST_CONTRACT_ID,
            "modelId": MODEL_ID,
            "promptVersion": PROMPT_VERSION,
            # grounding은 등록 코퍼스에서만 온다. 비면 검증기가 NO_GROUNDING으로 ABSTAIN한다.
            "publicEvidence": build_public_evidence(
                corpus_source, symbol=symbol, session_date=session_date
            ),
            "publicTimestamp": datetime.combine(
                session_date,
                datetime.min.time().replace(hour=9, minute=5),
                _KST,
            ).isoformat(),
            "sourceRegistryVersion": "p1-runtime-provider-gated-v1",
        }
    )


def _idempotency(run_id: str, operation: str) -> str:
    digest = __import__("hashlib").sha256(f"{run_id}:{operation}".encode()).hexdigest()
    return f"auto-rt-{operation}-{digest[:32]}"


def _positive_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("invalid number")
    text = str(value).replace(",", "")
    parsed = int(text)
    if parsed <= 0:
        raise ValueError("invalid number")
    return parsed


def _required(value: str | None) -> str:
    if not value:
        raise AutomationRuntimeError("AUTOMATION_SELECTION_MISSING")
    return value


def _required_side(value: str | None) -> Any:
    if value not in {"BUY", "SELL"}:
        raise AutomationRuntimeError("AUTOMATION_SELECTION_MISSING")
    return value


def _vertex_veto_transport() -> VertexVetoTransport:
    """설정이 있으면 실 Vertex를, 없으면 기존 fail-closed transport를 쓴다.

    미설정이 곧 ABSTAIN이고 ABSTAIN은 매수를 막으므로, 설정이 없는 쪽이 항상 더 안전하다.
    """

    settings = VertexTransportSettings.from_environment()
    if settings is None:
        return FailClosedVertexVetoTransport()
    return VertexAiVetoTransport(settings=settings)


def _open_position_value(balance: dict[str, Any]) -> int:
    """보유 포지션 평가액 합. live 잔고가 유일하게 신뢰할 수 있는 출처다."""

    positions = balance.get("positions")
    if not isinstance(positions, list):
        return 0
    total = 0
    for item in positions:
        if isinstance(item, dict):
            value = item.get("marketValueKrw")
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                total += value
    return total


def _risk_complete(balance: dict[str, Any], state: dict[str, Any]) -> bool:
    """RiskEngine이 요구하는 사실이 전부 확인됐을 때만 참이다.

    증거금은 모의 현금계좌라 0이 사실이고, 종목 분류는 카탈로그가 보유 종목을 전부 덮을 때만
    확인된 것으로 본다. 하나라도 모르면 거짓이고, 그러면 주문 산정이 열리지 않는다.
    """

    if balance.get("positionsComplete") is not True:
        return False
    if balance.get("marginRequirementKrw") != 0:
        return False
    catalog = state.get("instrumentCatalogSymbols")
    if not isinstance(catalog, list):
        return False
    classified = {str(symbol) for symbol in catalog}
    positions = balance.get("positions")
    if not isinstance(positions, list):
        return False
    for item in positions:
        if not isinstance(item, dict) or str(item.get("symbol", "")) not in classified:
            return False
    return True
