"""Spring bridge와 기존 KIS reader를 재사용하는 persistent automation live port."""

from __future__ import annotations

import json
import hashlib
import os
import re
import socket
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

import httpx
import psycopg
from pydantic import SecretStr

from app.brokerage.kis_mock_online_client import KISBrokerageCallBudget, KISMockBrokerageHttpClient
from app.brokerage.kis_mock_online_runtime import KISMockExecutionReader, KISMockOnlineBalanceReader
from app.brokerage.mock_order_reference_store import (
    EncryptedRedisOrderReferenceStore,
    MockProviderOrderReference,
)
from app.data._shared.canonical_json import canonical_json_bytes
from app.data.kis._credential_transport import _build_redis_client
from app.data.kis.http_client import ASKING_PRICE_PATH, CURRENT_PRICE_PATH, KISHttpClient
from app.data.kis.settings import KISSettings
from app.disclosure_repository import PostgresStoredDisclosureRepository
from app.p1_owner.world_news_corpus import (
    MergedCorpusDocumentSource,
    StoredWorldNewsArticle,
    WorldNewsCorpusDocumentSource,
)
from app.p1_owner.disclosure_corpus import DisclosureEventCorpusDocumentSource
from app.p1_owner.runtime_observation_publisher import publish_runtime_observations
from app.p1_owner.automation import (
    AccountLineageSnapshot,
    AiCandidateVerdict,
    AiJudgement,
    AutomationError,
    AutomationInputs,
    AutomationRun,
    CandidateScreening,
    EvidenceSpan,
    ExactOrderIntent,
    NewsVerdict,
    NewsScreeningBatch,
    SignalCandidate,
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
from app.p1_owner.vertex_source_registry import registered_source_for_uri
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
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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
    """numeric loopback와 per-install secret에 고정된 retry-0 internal Spring client다.

    shared secret은 이 다리가 loopback runtime의 것임을 증명할 뿐, 소유자를 증명하지 않는다.
    bridge 뒤의 brokerage·decision 서비스는 `AuthenticatedActorRef.current()`로 actor capability를
    발급하므로 인증된 소유자 세션이 없으면 모든 명령이 닫힌다. 그래서 다른 클라이언트와 똑같이
    소유자로 로그인해 access token을 붙인다. capability 사슬을 우회하지 않는다.

    token은 만료되므로 401을 만나면 한 번만 다시 로그인하고 재시도한다. 그 이상은 재시도하지
    않는다 — 주문 경로의 retry-0 경계를 지켜야 한다.
    """

    def __init__(
        self,
        shared_secret: str,
        *,
        transport: httpx.BaseTransport | None = None,
        owner_username: str | None = None,
        owner_password: str | None = None,
    ) -> None:
        if _SECRET.fullmatch(shared_secret) is None:
            raise AutomationRuntimeError("AUTOMATION_BRIDGE_SECRET_INVALID")
        self._secret = shared_secret
        self._owner_username = (
            owner_username
            if owner_username is not None
            else os.environ.get("P1_AUTOMATION_OWNER_USERNAME", "").strip()
        )
        self._owner_password = (
            owner_password
            if owner_password is not None
            else os.environ.get("P1_AUTOMATION_OWNER_PASSWORD", "").strip()
        )
        self._access_token: str | None = None
        self._client = httpx.Client(
            base_url="http://127.0.0.1:8080",
            transport=transport or httpx.HTTPTransport(retries=0),
            # Read timeout exceeds the downstream KIS I/O budget; loopback connect remains short.
            timeout=httpx.Timeout(connect=2.0, read=20.0, write=5.0, pool=2.0),
            follow_redirects=False,
            trust_env=False,
        )

    def _login(self) -> str:
        """소유자 세션을 한 번 연다. 자격이 없으면 명령을 시작하지 않는다."""

        if not self._owner_username or not self._owner_password:
            raise AutomationRuntimeError("AUTOMATION_BRIDGE_OWNER_CREDENTIAL_MISSING")
        response = self._client.post(
            "/api/v1/auth/login",
            content=canonical_json_bytes(
                {"password": self._owner_password, "username": self._owner_username}
            ),
            headers={"Content-Type": "application/json"},
        )
        if response.status_code != 200 or len(response.content) > 64 * 1024:
            raise AutomationRuntimeError("AUTOMATION_BRIDGE_OWNER_LOGIN_FAILED")
        try:
            parsed = response.json()
        except json.JSONDecodeError as error:
            raise AutomationRuntimeError("AUTOMATION_BRIDGE_OWNER_LOGIN_FAILED") from error
        token = (parsed.get("data") or {}).get("accessToken") if isinstance(parsed, dict) else None
        if not isinstance(token, str) or not token:
            raise AutomationRuntimeError("AUTOMATION_BRIDGE_OWNER_LOGIN_FAILED")
        self._access_token = token
        return token

    def _post_command(self, body: Mapping[str, object], token: str) -> httpx.Response:
        return self._client.post(
            "/internal/automation-runtime/command",
            content=canonical_json_bytes(body),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Automation-Runtime-Auth": self._secret,
            },
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
        token = self._access_token or self._login()
        response = self._post_command(body, token)
        if response.status_code == 401:
            # 만료된 세션은 한 번만 다시 연다. 같은 idempotency key로 다시 보내므로 중복 주문이
            # 생기지 않는다.
            response = self._post_command(body, self._login())
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


def kis_mock_connectivity_ready() -> bool:
    """Check the fixed Mock TLS origin without credentials or an API request."""
    host = "openapivts.koreainvestment.com"
    try:
        with socket.create_connection((host, 29443), timeout=4.0) as connection:
            with ssl.create_default_context().wrap_socket(connection, server_hostname=host):
                return True
    except OSError:
        return False


@dataclass(frozen=True, slots=True)
class OrderBookTop:
    """제출 순간의 최우선 호가. **기록 전용이다 - 가격 결정에 쓰지 않는다.**"""

    best_ask_krw: int
    best_ask_quantity: int
    best_bid_krw: int
    best_bid_quantity: int
    tr_id: str

    def projection(self) -> dict[str, object]:
        return {
            "bestAskKrw": self.best_ask_krw,
            "bestAskQuantity": self.best_ask_quantity,
            "bestBidKrw": self.best_bid_krw,
            "bestBidQuantity": self.best_bid_quantity,
            "trId": self.tr_id,
        }


class KisOrderBookSource:
    """제출 순간의 최우선 호가 한 쌍만 읽는다.

    체결 품질을 바꾸려면 근거가 있어야 하는데 지금은 아무 기록도 없다 - 스프레드가 얼마나
    넓은지, `last+1tick` 이 매도호가 뒤에 서는 빈도가 얼마인지 아무도 모른다. 이 조회는 그
    원장을 만들기 위한 것이고 **가격 결정에는 쓰이지 않는다.**

    선택된 한 종목을, 제출 **뒤에** 한 번만 부른다. 그래서 간격 제한기가 지연돼도 주문을
    늦추거나 순서를 바꾸지 못한다. 실패하면 `None` 이고 주문은 그대로 간다 -
    **원장이 거래를 막을 수 없어야 한다.**
    """

    def __init__(self) -> None:
        self._settings = KISSettings(kis_mode="mock", kis_offline=False, kis_retry_attempts=1)
        self._client = KISHttpClient(self._settings)

    def snapshot(self, symbol: str) -> OrderBookTop | None:
        try:
            payload = self._client.request(
                "GET",
                ASKING_PRICE_PATH,
                self._settings.asking_price_tr_id,
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
            )
        except Exception:
            return None
        output = payload.get("output1")
        if not isinstance(output, dict):
            return None
        try:
            ask = _positive_int(output.get("askp1"))
            bid = _positive_int(output.get("bidp1"))
            ask_quantity = int(str(output.get("askp_rsqn1") or 0))
            bid_quantity = int(str(output.get("bidp_rsqn1") or 0))
        except (ValueError, TypeError):
            return None
        # 장 마감·ETF 등에서 호가가 0 이거나 역전돼 나올 수 있다. 그때는 기록하지 않는다.
        if ask <= bid or ask_quantity < 0 or bid_quantity < 0:
            return None
        return OrderBookTop(
            best_ask_krw=ask,
            best_ask_quantity=ask_quantity,
            best_bid_krw=bid,
            best_bid_quantity=bid_quantity,
            tr_id=self._settings.asking_price_tr_id,
        )

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
        return Quote(
            symbol,
            price,
            lower,
            upper,
            fresh=True,
            is_etf_etn=False,
            temp_stop_yn=_quote_flag(output, "temp_stop_yn"),
            # inquire_price 는 관리종목 "여부" 를 mang_issu_cls_code 로, 시간외/마스터 계열은
            # mang_issu_yn 으로 내려준다. 한쪽만 읽으면 정상 종목이 미지값으로 떨어진다.
            management_issue_code=_quote_flag(output, "mang_issu_cls_code", "mang_issu_yn"),
            liquidation_trading_yn=_quote_flag(output, "sltr_yn"),
        )

    def close(self) -> None:
        self._client.close()


class KisAutomationExecutionSource:
    """Redis ciphertext reference와 공식 체결조회만 사용하며 row가 불명확하면 UNRESOLVED다."""

    def __init__(self) -> None:
        try:
            reference_ttl_seconds = int(
                os.environ.get("KIS_MOCK_ORDER_REFERENCE_TTL_SECONDS", "604800")
            )
        except ValueError:
            raise ValueError("KIS mock order reference TTL must be an integer") from None
        # Sizing balance + execution pages + post-fill balance share this client.
        # balance/read/post-fill을 주문별로 수행하되 세션 최대 3건을 넘지 않는다.
        # 주문 1건이 잔고·제출·체결·사후잔고로 3~4콜을 쓴다. 12콜은 곧 3건이라
        # `max_orders_per_session` 보다 이 예산이 먼저 세션을 묶었다. 세션 5건을
        # 실제로 낼 수 있도록 함께 올린다. 둘 중 하나만 올리면 효과가 없다.
        self._budget = KISBrokerageCallBudget(token_p_cap=1, brokerage_cap=20)
        self._redis = _build_redis_client()
        self._references = EncryptedRedisOrderReferenceStore(
            self._redis,
            encryption_key=SecretStr(os.environ.get("KIS_MOCK_ORDER_REFERENCE_KEY", "").strip()),
            ttl_seconds=reference_ttl_seconds,
        )
        self._client = KISMockBrokerageHttpClient(
            settings=KISSettings(kis_mode="mock", kis_offline=False, kis_retry_attempts=1),
            budget=self._budget,
        )
        self._reader = KISMockExecutionReader(self._client)
        self._balance_reader = KISMockOnlineBalanceReader(self._client)

    @property
    def physical_call_count(self) -> int:
        return sum(self._budget.counts.values())

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

    def recover_filled_buy(
        self,
        symbol: str,
        quantity: int,
        average_fill_price_krw: int,
        session_date: date,
    ) -> ReconcileSnapshot:
        snapshot = self._reader.recover_unique_filled_buy(
            symbol=symbol,
            quantity=quantity,
            average_fill_price_krw=average_fill_price_krw,
            session_date=session_date,
        )
        if snapshot is None:
            return ReconcileSnapshot(False, 0, quantity, None)
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
        order_book_source: KisOrderBookSource | None = None,
    ) -> None:
        self._claim = claim
        self._corpus_source: CorpusDocumentSource = corpus_source or EmptyCorpusDocumentSource()
        self._bridge = bridge
        self._quote_source = quote_source
        self._execution_source = execution_source
        self._vertex_transport = vertex_transport
        # 체결 품질 원장용. 없으면 기록을 건너뛴다 - 거래에는 영향이 없다.
        self._order_book = order_book_source
        self._cached_quotes: dict[str, Quote] = {}
        raw_screenings = state.get("screenings")
        if isinstance(raw_screenings, list):
            for item in raw_screenings:
                if isinstance(item, dict) and isinstance(item.get("symbol"), str):
                    self._cached_quotes[str(item["symbol"])] = Quote(
                        str(item["symbol"]),
                        int(item["priceKrw"]),
                        int(item["lowerLimitKrw"]),
                        int(item["upperLimitKrw"]),
                        is_etf_etn=bool(item["isEtfEtn"]),
                    )
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
        self.judge_calls = 0
        self.last_judgement_json: str | None = None
        self.submit_calls = int(state.get("logicalSubmitCount", 0))
        self.reconcile_calls = 0
        self.cancel_calls = 0
        self._last_execution_ref_hash: str | None = None
        self._completed_balance: dict[str, object] | None = None
        self._latest_balance: dict[str, object] | None = None
        self._latest_buyables: dict[str, dict[str, object]] = {}
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
        runtime_state["aiJudgementProviderBound"] = state.get("aiProviderReady") is True
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
                    "runId": self._claim.run_id,
                    "claimTokenHash": self._claim.claim_token_hash,
                    "evaluation": {
                        "principleId": self._claim.principle_id,
                        "portfolioSource": "KIS_MOCK",
                        "orderIntent": reservation.intent.projection(),
                    },
                },
                idempotency_key=_idempotency(
                    self._claim.run_id,
                    "decision"
                    if not state.get("decisionEpoch")
                    else f"decision:revision:{int(state['decisionEpoch'])}",
                ),
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
            self._latest_balance = dict(balance)
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
                self._latest_buyables[symbol] = dict(buyable)
            # 다음 tick 이 RISK_CHECKING 이고 RiskEngine 은 관측 표에서 잔고와 위험지표를
            # 읽는다. 그 표를 채우는 것이 운영자 CLI 뿐이어서, 사람이 매일 손으로 적재하지
            # 않으면 `violations` 는 비어 있는데 입력 부재로 HOLD 됐다. 잔고를 받는 프로세스는
            # 이 런타임뿐이므로 여기서 같은 tick 안에 적재한다. 실패는 마커만 남기고 삼킨다 -
            # 관측이 없으면 RiskEngine 이 HOLD 하므로 결과가 이미 fail-closed 이고, 예외를
            # 올리면 세션이 HALTED 로 닫혀 사람이 손대야 다시 열린다.
            runtime_state["observationPublish"] = publish_runtime_observations(
                owner_user_id=self._claim.user_id,
                account_id=self._claim.account_id,
                balance=balance,
                # 시세 관측의 신선도 창은 관측 +5분이라 일일 수집기 값으로는 장중 판정을
                # 만족할 수 없다. 지금 주문하려는 종목과 보유 종목의 실시간 호가는 이 시점에
                # 이 프로세스에만 있다. 그것을 같은 tick 안에 적재해야 RiskEngine 의
                # current_price_krw / order_amount_krw / asset_weight 가 값을 갖는다.
                quotes=self._observation_quotes(symbol, balance),
                baseline_equity_krw=_projection_equity(expected, balance),
                trading_date=self._claim.session_date.isoformat(),
            )
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

    def screen(
        self,
        candidates: tuple[SignalCandidate, ...],
        quotes: Mapping[str, Quote],
        candidate_set_sha256: str,
    ) -> NewsScreeningBatch:
        # Reserve the worst-case single grounded call before the bridge can
        # open any provider socket.  The returned count only consumes the
        # reservation; it never authorizes an over-cap call retroactively.
        self._require_capacity(1)
        response = self._bridge.command(
            "NEWS_SCREEN",
            self._claim.user_id,
            _evidence_candidates_payload(
                self._claim,
                candidates,
                quotes,
                candidate_set_sha256,
                _stored_evidence_payload(
                    self._corpus_source,
                    candidates=candidates,
                    session_date=self._claim.session_date,
                ),
            ),
        )
        provider_calls = int(response.get("providerCallCount", -1))
        grounding_queries = int(response.get("groundingQueryCount", -1))
        if provider_calls not in range(0, 2) or grounding_queries not in range(0, 33):
            raise AutomationRuntimeError("AUTOMATION_SCREENING_USAGE_INVALID")
        raw = response.get("screenings")
        if not isinstance(raw, list):
            raise AutomationRuntimeError("AUTOMATION_SCREENING_INVALID")
        screenings = tuple(_candidate_screening(item) for item in raw if isinstance(item, dict))
        if len(screenings) != len(raw):
            raise AutomationRuntimeError("AUTOMATION_SCREENING_INVALID")
        self.physical_calls += provider_calls
        return NewsScreeningBatch(
            screenings,
            provider_call_count=provider_calls,
            grounding_query_count=grounding_queries,
            failed=response.get("failed") is True,
        )

    def judge(
        self,
        candidates: tuple[SignalCandidate, ...],
        candidate_set_sha256: str,
    ) -> AiJudgement | None:
        if not candidates:
            return None
        self.judge_calls += 1
        # JUDGE may use a primary and one bounded fallback call.  Refuse before
        # contacting Spring unless both calls fit in the run cap.
        self._require_capacity(2)
        try:
            response = self._bridge.command(
                "JUDGE",
                self._claim.user_id,
                _evidence_candidates_payload(
                    self._claim,
                    candidates,
                    self._cached_quotes,
                    candidate_set_sha256,
                    _stored_evidence_payload(
                        self._corpus_source,
                        candidates=candidates,
                        session_date=self._claim.session_date,
                    ),
                ),
            )
            raw_candidates = response.get("candidates")
            if not isinstance(raw_candidates, list):
                return None
            verdicts = tuple(
                AiCandidateVerdict(
                    symbol=str(item["symbol"]),
                    score=int(item["scoreBps"]) / 10_000,
                    veto=item.get("veto") is True,
                    reason=str(item["reason"]),
                    evidence_spans=tuple(
                        (str(span["citationId"]), str(span["quote"]))
                        for span in item.get("evidenceSpans", [])
                        if isinstance(span, dict)
                    ),
                )
                for item in raw_candidates
                if isinstance(item, dict)
            )
            provider_calls = int(response.get("providerCallCount", 0))
            if provider_calls not in range(0, 3):
                raise AutomationRuntimeError("AUTOMATION_JUDGEMENT_USAGE_INVALID")
            self.physical_calls += provider_calls
            applied = AiJudgement(
                verdicts,
                str(response["summary"]),
            )
        except (AutomationError, AutomationRuntimeError, KeyError, TypeError, ValueError):
            return None
        self.last_judgement_json = json.dumps(
            response,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return applied

    def _observation_quotes(self, symbol: str, balance: Mapping[str, object]) -> dict[str, int]:
        """관측으로 적재할 실시간 호가. 주문 종목과 보유 종목을 함께 담는다.

        `asset_weight` 는 보유 비중이라 보유 종목의 가격이 있어야 계산된다. 브로커가 준
        평가액을 수량으로 나눠 단가를 만든다 - 여기서 가격을 지어내지 않는다.
        """

        quotes: dict[str, int] = {}
        cached = self._cached_quotes.get(symbol)
        if cached is not None and cached.price_krw > 0:
            quotes[symbol] = cached.price_krw
        raw_positions = balance.get("positions")
        if isinstance(raw_positions, list):
            for item in raw_positions:
                if not isinstance(item, Mapping):
                    continue
                held = str(item.get("symbol", ""))
                quantity = int(item.get("quantity", 0) or 0)
                market_value = int(item.get("marketValueKrw", 0) or 0)
                if held and quantity > 0 and market_value > 0:
                    quotes.setdefault(held, market_value // quantity)
        return quotes

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

    def portfolio_balance(self) -> dict[str, object]:
        """추가 ordinal 계획에는 최신 KIS_MOCK balance를 새로 읽어 이전 매도대금을 추정하지 않는다."""

        self._require_capacity(1)
        balance = self._execution_source.balance(self._claim.account_id)
        self.physical_calls += 1
        if balance.get("accountId") != self._claim.account_id:
            raise AutomationRuntimeError("AUTOMATION_BALANCE_IDENTITY_MISMATCH")
        self._latest_balance = dict(balance)
        return dict(balance)

    def portfolio_quote(self, symbol: str) -> Quote:
        return self._quote(symbol)

    def portfolio_order_book(self, symbol: str) -> dict[str, object] | None:
        """제출 순간의 최우선 호가. **기록 전용이고 가격 결정에 쓰지 않는다.**

        `KISBrokerageCallBudget` 을 쓰지 않는다 - 시세는 `KISHttpClient` 로 가고 그쪽엔 예산
        객체가 없다. 실제 비용은 공유 간격 제한기 슬롯 하나이고, 제출 뒤에 부르므로 주문을
        늦추지 못한다. 실패하면 None 이다.
        """

        source = self._order_book
        if source is None:
            return None
        snapshot = source.snapshot(symbol)
        return snapshot.projection() if snapshot is not None else None

    def portfolio_buyable(self, symbol: str, estimated_price: int) -> dict[str, object]:
        self._require_capacity(1)
        result = self._bridge.command(
            "BUYABLE",
            self._claim.user_id,
            {
                "accountId": self._claim.account_id,
                "estimatedPrice": estimated_price,
                "symbol": symbol,
            },
        )
        self.physical_calls += 1
        if (
            result.get("accountId") != self._claim.account_id
            or result.get("symbol") != symbol
            or result.get("estimatedPrice") != estimated_price
        ):
            raise AutomationRuntimeError("AUTOMATION_BUYABLE_IDENTITY_MISMATCH")
        self._latest_buyables[symbol] = dict(result)
        return dict(result)

    def portfolio_evaluate(self, intent: ExactOrderIntent, ordinal: int) -> str | None:
        """각 exact intent를 기존 Decision/RiskEngine에서 다시 평가하며 HOLD는 주문으로 승격하지 않는다."""

        response = self._bridge.command(
            "EVALUATE",
            self._claim.user_id,
            {
                "runId": self._claim.run_id,
                "claimTokenHash": self._claim.claim_token_hash,
                "evaluation": {
                    "principleId": self._claim.principle_id,
                    "portfolioSource": "KIS_MOCK",
                    "orderIntent": intent.projection(),
                },
            },
            idempotency_key=_idempotency(self._claim.run_id, f"portfolio-decision:{ordinal}"),
        )
        risk = response.get("riskDecision")
        decision_id = response.get("decisionId")
        if not isinstance(risk, dict) or not isinstance(decision_id, str):
            raise AutomationRuntimeError("AUTOMATION_DECISION_INVALID")
        return (
            decision_id
            if risk.get("decision") == "ALLOW" and risk.get("canSubmitOrder") is True
            else None
        )

    def portfolio_submit(
        self,
        intent: ExactOrderIntent,
        *,
        decision_id: str,
        ordinal: int,
    ) -> dict[str, object]:
        """DB begin이 SUBMIT을 반환한 ordinal만 기존 KIS_MOCK submit bridge로 한 번 보낸다."""

        self._require_capacity(1)
        response = self._bridge.command(
            "SUBMIT",
            self._claim.user_id,
            {
                "decisionId": decision_id,
                "orderIntent": intent.projection(),
                "userAcknowledgement": {"warningsAccepted": False},
            },
            idempotency_key=_idempotency(self._claim.run_id, f"portfolio-submit:{ordinal}"),
        )
        self.physical_calls += 1
        return dict(response)

    def portfolio_reconcile(self, order_id: str) -> ReconcileSnapshot:
        self._require_capacity(1)
        self.physical_calls += 1
        result = self._execution_source.read(
            order_id, self._claim.account_id, self._claim.session_date
        )
        if not isinstance(result, ReconcileSnapshot):
            raise AutomationRuntimeError("AUTOMATION_PORTFOLIO_RECONCILIATION_INVALID")
        return result

    def reconcile(
        self, reservation: OrderReservation | None
    ) -> ReconcileOutcome | ReconcileSnapshot:
        del reservation
        self.reconcile_calls += 1
        if self.order_id is None:
            return "UNRESOLVED"
        self._require_capacity(1)
        self.physical_calls += 1
        snapshot = self._execution_source.read(
            self.order_id,
            self._claim.account_id,
            self._claim.session_date,
        )
        self._last_execution_ref_hash = (
            snapshot.provider_exec_ref_hash if isinstance(snapshot, ReconcileSnapshot) else None
        )
        return snapshot

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
        self._completed_balance = dict(balance)
        return AccountLineageAdvance(
            reason="BUY_FILL" if side == "BUY" else "SELL_FILL",
            projection=projection,
            digest=observed.digest,
            order_id=self.order_id,
            filled_quantity=filled_quantity,
            average_fill_price_krw=average_fill_price_krw,
            provider_exec_ref_hash=self._last_execution_ref_hash,
        )

    def publish_completed_observations(self) -> None:
        if self._completed_balance is not None:
            marker = publish_runtime_observations(
                owner_user_id=self._claim.user_id,
                account_id=self._claim.account_id,
                balance=self._completed_balance,
                baseline_equity_krw=0,
                trading_date=self._claim.session_date.isoformat(),
                publish_risk_metrics=False,
            )
            print(f"AUTOMATION_COMPLETED_BALANCE={marker}", flush=True)

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
        if self._order_book is not None:
            self._order_book.close()

    def _quote(self, symbol: str | None) -> Quote:
        selected = _required(symbol)
        if selected not in self._cached_quotes:
            if len(self._cached_quotes) >= 31:
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
        if calls < 1 or self.physical_calls > 64 - calls:
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
            _corpus_source(),
            KisOrderBookSource(),
        )


# 판단 요청의 질문은 고정이다. 사용자 문장이 여기로 들어오면 그것이 매매 판단을 바꾸는
# 통로가 되고, 그러면 프롬프트 인젝션이 곧 주문 조작이 된다.
def _evidence_candidates_payload(
    claim: RuntimeClaim,
    candidates: tuple[SignalCandidate, ...],
    quotes: Mapping[str, Quote],
    candidate_set_sha256: str,
    stored_evidence: tuple[dict[str, object], ...] = (),
) -> dict[str, object]:
    if _SHA256.fullmatch(candidate_set_sha256) is None:
        raise AutomationRuntimeError("AUTOMATION_CANDIDATE_SET_HASH_INVALID")
    return {
        "candidateSetSha256": candidate_set_sha256,
        "candidates": [
            {
                "expectedReturn": format(item.expected_return, ".17g"),
                "isEtfEtn": quotes[item.symbol].is_etf_etn,
                "lowerLimitKrw": quotes[item.symbol].lower_limit_krw,
                "priceKrw": quotes[item.symbol].price_krw,
                "symbol": item.symbol,
                "upperLimitKrw": quotes[item.symbol].upper_limit_krw,
            }
            for item in candidates
            if item.symbol in quotes
        ],
        "runId": claim.run_id,
        "sessionDate": claim.session_date.isoformat(),
        "storedEvidence": list(stored_evidence),
    }


def _stored_evidence_payload(
    source: CorpusDocumentSource,
    *,
    candidates: tuple[SignalCandidate, ...],
    session_date: date,
) -> tuple[dict[str, object], ...]:
    """기존 저장 공시 코퍼스를 Spring Strong LLM용 bounded evidence로 투영한다."""

    output: list[dict[str, object]] = []
    for candidate in candidates:
        public_items = build_public_evidence(
            source, symbol=candidate.symbol, session_date=session_date
        )
        documents = source.documents(symbol=candidate.symbol, session_date=session_date)
        for evidence in public_items:
            source_id = str(evidence["sourceId"])
            source_type = str(evidence["sourceType"])
            source_event_date = str(evidence["sourceEventDate"])
            bounded_quote = str(evidence["boundedQuote"])
            document = next(
                (
                    item
                    for item in documents
                    if registered_source_for_uri(item.uri) == (source_id, source_type)
                    and item.published_on.isoformat() == source_event_date
                    and " ".join(item.passage.split())[:240] == bounded_quote
                ),
                None,
            )
            if document is None:
                continue
            digest = hashlib.sha256(
                f"{candidate.symbol}|{source_id}|{document.uri}|{bounded_quote}".encode()
            ).hexdigest()
            output.append(
                {
                    "boundedQuote": bounded_quote,
                    "citationId": f"cit_stored_{digest[:32]}",
                    "sourceEventDate": source_event_date,
                    "sourceId": source_id,
                    "sourceType": source_type,
                    "supportObserved": True,
                    "symbol": candidate.symbol,
                    "uri": document.uri,
                }
            )
    output.sort(key=lambda item: (str(item["symbol"]), str(item["citationId"])))
    return tuple(output[:155])


def _candidate_screening(value: dict[str, Any]) -> CandidateScreening:
    raw_evidence = value.get("evidence")
    if not isinstance(raw_evidence, list):
        raise AutomationRuntimeError("AUTOMATION_SCREENING_INVALID")
    evidence = tuple(
        EvidenceSpan(
            symbol=str(item["symbol"]),
            citation_id=str(item["citationId"]),
            source_id=str(item["sourceId"]),
            source_type=cast(Any, str(item["sourceType"])),
            source_event_date=(
                date.fromisoformat(str(item["sourceEventDate"]))
                if item.get("sourceEventDate")
                else None
            ),
            age_warning=item.get("ageWarning") is True,
            uri_sha256=str(item["uriSha256"]),
            bounded_quote=str(item["boundedQuote"]),
            quote_sha256=str(item["quoteSha256"]),
            verified=item.get("verified") is True,
        )
        for item in raw_evidence
        if isinstance(item, dict)
    )
    if len(evidence) != len(raw_evidence):
        raise AutomationRuntimeError("AUTOMATION_SCREENING_INVALID")
    return CandidateScreening(
        symbol=str(value["symbol"]),
        status=cast(Any, str(value["status"])),
        verdict=cast(Any, str(value["verdict"])),
        score_bps=int(value["scoreBps"]),
        reason=str(value["reason"]),
        evidence=evidence,
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


def _quote_flag(output: dict[str, Any], *keys: str) -> str:
    """Return the first present KIS trading-status flag, or an explicit UNKNOWN.

    빈 문자열로 폴백하면 판정 쪽에서 거래정지와 구별되지 않아 전 종목이 조용히
    탈락한다. 누락은 누락으로 표기해 사유가 로그와 화면까지 도달하게 한다.
    """

    for key in keys:
        value = output.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return "UNKNOWN"


def _required(value: str | None) -> str:
    if not value:
        raise AutomationRuntimeError("AUTOMATION_SELECTION_MISSING")
    return value


def _required_side(value: str | None) -> Any:
    if value not in {"BUY", "SELL"}:
        raise AutomationRuntimeError("AUTOMATION_SELECTION_MISSING")
    return value


def _corpus_source() -> CorpusDocumentSource:
    """공시 투영이 있으면 그것을 근거 코퍼스로, 없으면 기존 빈 코퍼스를 쓴다.

    빈 코퍼스가 곧 근거 0개이고 근거 0개는 ABSTAIN 이므로 매수를 막는다 - 설정이 없는 쪽이
    항상 더 안전하다. 이 자리가 계획이 말한 "빠진 클래스 하나"였다.

    reader 는 자기 DSN 으로 role 과 표 권한을 실제로 확인한다(`_attest_reader_dsn`).
    그 검증이나 연결이 실패하면 여기서 빈 코퍼스로 내려앉고 표식을 남긴다 - 런 중에 예외를
    던지면 tick 하나가 근거 조회 때문에 죽는다.
    """

    dsn = os.environ.get("DECISION_DISCLOSURE_READER_DATABASE_DSN", "").strip()
    if not dsn:
        print("AUTOMATION_DISCLOSURE_CORPUS=UNCONFIGURED", flush=True)
        return EmptyCorpusDocumentSource()
    try:
        repository = PostgresStoredDisclosureRepository(dsn)
    except (ValueError, OSError, psycopg.Error) as error:
        print(
            f"AUTOMATION_DISCLOSURE_CORPUS=UNAVAILABLE error={type(error).__name__}",
            flush=True,
        )
        return EmptyCorpusDocumentSource()
    print("AUTOMATION_DISCLOSURE_CORPUS=READY", flush=True)
    disclosure = DisclosureEventCorpusDocumentSource(_LoggingDisclosureLoader(repository))
    world_news = _world_news_corpus_source(dsn)
    if world_news is None:
        return disclosure
    # 도메인당 한 건만 쓰이므로 공시(dart.fss)와 언론 기사가 서로를 밀어내지 않는다.
    return MergedCorpusDocumentSource(sources=(disclosure, world_news))


def _world_news_corpus_source(dsn: str) -> WorldNewsCorpusDocumentSource | None:
    """세계 뉴스 근거 원천. 읽을 수 없으면 None 이고, 그러면 공시 근거만 쓴다.

    근거가 줄면 ABSTAIN 쪽으로 기울 뿐이고 ABSTAIN 은 통과다 - 뉴스를 못 읽는다고 해서
    매매가 멈추지는 않는다.
    """

    try:
        loader = _PostgresWorldNewsLoader(dsn)
        names = _PostgresInstrumentNameLoader(dsn)
    except (ValueError, OSError, psycopg.Error) as error:
        print(
            f"AUTOMATION_WORLD_NEWS_CORPUS=UNAVAILABLE error={type(error).__name__}",
            flush=True,
        )
        return None
    print("AUTOMATION_WORLD_NEWS_CORPUS=READY", flush=True)
    return WorldNewsCorpusDocumentSource(loader=loader, names=names)


@dataclass(frozen=True, slots=True)
class _PostgresWorldNewsLoader:
    """V177 의 근거 전용 읽기만 부른다. 화면 조회 함수(lookup_allowed)는 쓰지 않는다."""

    dsn: str

    def load(
        self, *, window_from: date, window_to: date, limit: int
    ) -> tuple[StoredWorldNewsArticle, ...]:
        with psycopg.connect(self.dsn, connect_timeout=5) as connection:
            rows = connection.execute(
                "select canonical_url,title,bounded_quote,bounded_passage,first_seen_at "
                "from p1_read_world_news_evidence_v1(%s,%s,%s,%s)",
                ("", window_from, window_to, min(limit, 500)),
            ).fetchall()
        return tuple(
            StoredWorldNewsArticle(
                canonical_url=str(row[0]),
                title=str(row[1] or ""),
                bounded_quote=(str(row[2]) if row[2] else None),
                bounded_passage=(str(row[3]) if row[3] else None),
                published_at=None,
                publication_status="",
                first_seen_at=row[4],
            )
            for row in rows
        )


@dataclass(frozen=True, slots=True)
class _PostgresInstrumentNameLoader:
    """종목의 정식 표시명. 없으면 그 종목은 뉴스 근거를 갖지 않는다."""

    dsn: str

    def official_name(self, *, symbol: str) -> str | None:
        with psycopg.connect(self.dsn, connect_timeout=5) as connection:
            row = connection.execute(
                "select p1_read_instrument_display_name_v1(%s)",
                (symbol,),
            ).fetchone()
        if row is None or not row[0]:
            return None
        return str(row[0])


@dataclass(frozen=True, slots=True)
class _LoggingDisclosureLoader:
    """읽기 실패를 런 밖으로 던지지 않고 빈 배치로 바꾼다.

    근거를 못 읽는 것은 "근거가 없다"와 같은 결과(ABSTAIN)여야 하고, tick 을 죽이는 것과는
    다르다. 다만 조용히 삼키지 않으려고 표식을 남긴다.
    """

    repository: PostgresStoredDisclosureRepository

    def load(
        self,
        *,
        symbol: str,
        corp_code: str | None,
        window_from: date,
        window_to: date,
    ) -> Any:
        try:
            batch = self.repository.load_optional_events(
                symbol=symbol,
                corp_code=corp_code,
                window_from=window_from,
                window_to=window_to,
            )
        except Exception as error:  # noqa: BLE001 - 근거 조회가 tick 을 죽이지 않는다
            print(
                f"AUTOMATION_DISCLOSURE_CORPUS=READ_FAILED symbol={symbol} "
                f"error={type(error).__name__}",
                flush=True,
            )
            return _EmptyDisclosureBatch()
        print(
            f"AUTOMATION_DISCLOSURE_CORPUS=READ symbol={symbol} "
            f"events={len(batch.events)} complete={str(batch.complete).lower()} "
            f"collectionStatus={batch.collection_status}",
            flush=True,
        )
        return batch


@dataclass(frozen=True, slots=True)
class _EmptyDisclosureBatch:
    events: tuple[Any, ...] = ()
    complete: bool = False
    collection_status: str = "READ_FAILED"


def _vertex_veto_transport() -> VertexVetoTransport:
    """설정이 있으면 실 Vertex를, 없으면 기존 fail-closed transport를 쓴다.

    미설정이 곧 ABSTAIN이고 ABSTAIN은 매수를 막으므로, 설정이 없는 쪽이 항상 더 안전하다.
    """

    settings = VertexTransportSettings.from_environment()
    if settings is None:
        return FailClosedVertexVetoTransport()
    return VertexAiVetoTransport(settings=settings)


def _projection_equity(expected: Mapping[str, Any], balance: Mapping[str, Any]) -> int:
    """세션 기준 자본. 위험지표의 분모다.

    durable state 의 projection 은 평가액이 없는 identity(현금 + 수량)이므로 기준 현금에
    현재 포지션 평가액을 더한다. 주문 전에는 포지션이 그대로이므로 이것이 그날 개장 자본이고,
    매수로 현금이 포지션으로 바뀌어도 합이 유지되어 매수 자체가 손실로 잡히지 않는다.
    """

    baseline_cash = int(expected.get("cashKrw", 0))
    equity = baseline_cash + _open_position_value(dict(balance))
    # 분모가 0 이면 비율이 정의되지 않는다. 그 경우는 관측을 만들지 않는 편이 정직하다.
    return equity if equity > 0 else 0


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
