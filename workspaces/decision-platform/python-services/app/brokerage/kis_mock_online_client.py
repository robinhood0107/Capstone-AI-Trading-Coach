"""S1.1 인증·Redis quota를 재사용하는 KIS_MOCK 전용 brokerage transport."""

from __future__ import annotations

import re
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Any, cast

import httpx
from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.data.kis._credential_transport import (
    KISCredentialError,
    KISResponseTooLargeError,
    _CredentialTransport,
    _TokenIssuer,
    _build_redis_client,
    _provider_scope,
)
from app.data.kis.accounting import KISCallBudgetExceeded
from app.data.kis.auth import KISTokenCacheError, KISTokenManager
from app.data.kis.rate_limiter import (
    KISRateLimitUnavailable,
    KISRateLimitWaitExceeded,
    RateLimiter,
    RedisIntervalLimiter,
    TokenBucket,
)
from app.data.kis.settings import KISSettings

ORDER_CASH_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
ORDER_CANCEL_PATH = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
BUYABLE_PATH = "/uapi/domestic-stock/v1/trading/inquire-psbl-order"
EXECUTIONS_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"

MOCK_BUY_TR_ID = "VTTC0012U"
MOCK_SELL_TR_ID = "VTTC0011U"
MOCK_CANCEL_TR_ID = "VTTC0013U"
MOCK_BALANCE_TR_ID = "VTTC8434R"
MOCK_BUYABLE_TR_ID = "VTTC8908R"
MOCK_EXECUTIONS_RECENT_TR_ID = "VTTC0081R"
MOCK_EXECUTIONS_ARCHIVE_TR_ID = "VTSC9215R"

_APPROVED_OPERATIONS = frozenset(
    {
        ("POST", ORDER_CASH_PATH, MOCK_BUY_TR_ID),
        ("POST", ORDER_CASH_PATH, MOCK_SELL_TR_ID),
        ("POST", ORDER_CANCEL_PATH, MOCK_CANCEL_TR_ID),
        ("GET", BALANCE_PATH, MOCK_BALANCE_TR_ID),
        ("GET", BUYABLE_PATH, MOCK_BUYABLE_TR_ID),
        ("GET", EXECUTIONS_PATH, MOCK_EXECUTIONS_RECENT_TR_ID),
        ("GET", EXECUTIONS_PATH, MOCK_EXECUTIONS_ARCHIVE_TR_ID),
    }
)
_POST_FIELDS = {
    (ORDER_CASH_PATH, MOCK_BUY_TR_ID): {
        "PDNO",
        "ORD_DVSN",
        "ORD_QTY",
        "ORD_UNPR",
    },
    (ORDER_CASH_PATH, MOCK_SELL_TR_ID): {
        "PDNO",
        "ORD_DVSN",
        "ORD_QTY",
        "ORD_UNPR",
    },
    (ORDER_CANCEL_PATH, MOCK_CANCEL_TR_ID): {
        "KRX_FWDG_ORD_ORGNO",
        "ORGN_ODNO",
        "ORD_DVSN",
        "RVSE_CNCL_DVSN_CD",
        "ORD_QTY",
        "ORD_UNPR",
        "QTY_ALL_ORD_YN",
        "EXCG_ID_DVSN_CD",
    },
}
_GET_FIELDS = {
    (BALANCE_PATH, MOCK_BALANCE_TR_ID): {
        "AFHR_FLPR_YN",
        "OFL_YN",
        "INQR_DVSN",
        "UNPR_DVSN",
        "FUND_STTL_ICLD_YN",
        "FNCG_AMT_AUTO_RDPT_YN",
        "PRCS_DVSN",
        "CTX_AREA_FK100",
        "CTX_AREA_NK100",
    },
    (BUYABLE_PATH, MOCK_BUYABLE_TR_ID): {
        "PDNO",
        "ORD_UNPR",
        "ORD_DVSN",
        "CMA_EVLU_AMT_ICLD_YN",
        "OVRS_ICLD_YN",
    },
    (EXECUTIONS_PATH, MOCK_EXECUTIONS_RECENT_TR_ID): {
        "INQR_STRT_DT",
        "INQR_END_DT",
        "SLL_BUY_DVSN_CD",
        "INQR_DVSN",
        "PDNO",
        "CCLD_DVSN",
        "ORD_GNO_BRNO",
        "ODNO",
        "INQR_DVSN_3",
        "INQR_DVSN_1",
        "CTX_AREA_FK100",
        "CTX_AREA_NK100",
        "EXCG_ID_DVSN_CD",
    },
    (EXECUTIONS_PATH, MOCK_EXECUTIONS_ARCHIVE_TR_ID): {
        "INQR_STRT_DT",
        "INQR_END_DT",
        "SLL_BUY_DVSN_CD",
        "INQR_DVSN",
        "PDNO",
        "CCLD_DVSN",
        "ORD_GNO_BRNO",
        "ODNO",
        "INQR_DVSN_3",
        "INQR_DVSN_1",
        "CTX_AREA_FK100",
        "CTX_AREA_NK100",
        "EXCG_ID_DVSN_CD",
    },
}
_ACCOUNT_PATTERN = re.compile(r"^[0-9]{8}-[0-9]{2}$")
_EXCHANGE_DIVISION = re.compile(r"^(?:KRX|NXT)$")
_INTERNAL_TR_ID_HEADER = "x-kis-internal-tr-id"
_MAX_RESPONSE_BYTES = 1024 * 1024
_ROOT_ENV_FILE = Path(__file__).resolve().parents[5] / ".env"
_PROVIDER_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,31}$")


class KISMockLiveOrderGateClosed(RuntimeError):
    """실전 주문 mode나 실전 주문 TR은 provider send 전에 영구 거부한다."""


class KISMockFailureReason(StrEnum):
    """원문 없이 operator에게 공개할 수 있는 KIS_MOCK 실패 분류 allowlist다."""

    CREDENTIAL_UNAVAILABLE = "BROKERAGE_CREDENTIAL_UNAVAILABLE"
    RATE_LIMIT_UNAVAILABLE = "BROKERAGE_RATE_LIMIT_UNAVAILABLE"
    CALL_BUDGET_EXCEEDED = "BROKERAGE_CALL_BUDGET_EXCEEDED"
    TRANSPORT_UNAVAILABLE = "BROKERAGE_TRANSPORT_UNAVAILABLE"
    HTTP_ERROR = "BROKERAGE_HTTP_ERROR"
    RESPONSE_TOO_LARGE = "BROKERAGE_RESPONSE_TOO_LARGE"
    RESPONSE_INVALID = "BROKERAGE_RESPONSE_INVALID"
    RESPONSE_SANITIZATION_FAILED = "BROKERAGE_RESPONSE_SANITIZATION_FAILED"
    PROVIDER_REJECTED = "BROKERAGE_PROVIDER_REJECTED"
    BALANCE_PAGINATION_REQUIRED = "BALANCE_PAGINATION_REQUIRED"
    BALANCE_POSITIONS_INVALID = "BALANCE_POSITIONS_INVALID"
    BALANCE_SUMMARY_INVALID = "BALANCE_SUMMARY_INVALID"
    BALANCE_CASH_INVALID = "BALANCE_CASH_INVALID"
    BALANCE_EQUITY_INVALID = "BALANCE_EQUITY_INVALID"
    BALANCE_RISK_FIELDS_UNAVAILABLE = "BALANCE_RISK_FIELDS_UNAVAILABLE"
    BALANCE_PROBE_RESPONSE_INVALID = "BALANCE_PROBE_RESPONSE_INVALID"
    BUYABLE_PROBE_UNFUNDED = "BUYABLE_PROBE_UNFUNDED"
    ORDER_PROBE_REJECTED = "ORDER_PROBE_REJECTED"
    ORDER_REFERENCE_COMMIT_COMPENSATED = "ORDER_REFERENCE_COMMIT_COMPENSATED"
    ORDER_OUTCOME_UNCERTAIN = "ORDER_OUTCOME_UNCERTAIN"
    CANCEL_PROBE_UNCONFIRMED = "CANCEL_PROBE_UNCONFIRMED"
    EXECUTION_REFERENCE_UNAVAILABLE = "EXECUTION_REFERENCE_UNAVAILABLE"
    RUNTIME_INIT_FAILED = "RUNTIME_INIT_FAILED"
    RUNTIME_CLOSE_FAILED = "RUNTIME_CLOSE_FAILED"
    UNCLASSIFIED_FAILURE = "UNCLASSIFIED_FAILURE"


class KISMockBrokerageError(RuntimeError):
    """provider 원문을 보존하지 않는 stable brokerage transport 오류다."""

    def __init__(
        self,
        reason: KISMockFailureReason,
        *,
        provider_code: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__("KIS_MOCK brokerage request failed")
        self.reason_code = reason.value
        self.provider_code = _bounded_provider_code(provider_code)
        self.http_status = (
            http_status
            if type(http_status) is int and 100 <= http_status <= 599
            else None
        )


class KISBrokerageCallBudgetExceeded(KISCallBudgetExceeded):
    """승인 packet의 token/brokerage reservation cap을 outbound 전에 강제한다."""


class KISBrokerageCallBudget:
    """한 승인 실행의 tokenP와 brokerage physical reservation을 thread-safe하게 센다."""

    def __init__(self, *, token_p_cap: int, brokerage_cap: int) -> None:
        if (
            type(token_p_cap) is not int
            or type(brokerage_cap) is not int
            or token_p_cap < 0
            or brokerage_cap < 0
        ):
            raise ValueError("KIS brokerage physical caps must be non-negative integers")
        self._caps = {"tokenP": token_p_cap, "brokerage": brokerage_cap}
        self._counts = {"tokenP": 0, "brokerage": 0}
        self._lock = Lock()

    def reserve_token_p(self) -> None:
        self._reserve("tokenP")

    def reserve_brokerage(self) -> None:
        self._reserve("brokerage")

    @property
    def counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def _reserve(self, channel: str) -> None:
        with self._lock:
            if self._counts[channel] >= self._caps[channel]:
                raise KISBrokerageCallBudgetExceeded(
                    f"KIS {channel} physical reservation cap exhausted"
                )
            self._counts[channel] += 1


class _KISMockBrokerageSecrets(BaseSettings):
    """계좌번호는 final brokerage client에서만 SecretStr로 읽는다."""

    model_config = SettingsConfigDict(
        env_file=(_ROOT_ENV_FILE,),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    kis_mock_account_no: SecretStr = Field(repr=False, exclude=True)


class _BrokerageBudgetTransport(httpx.BaseTransport):
    """승인 cap을 token, shared limiter, socket handoff보다 앞에서 소비한다."""

    def __init__(
        self,
        inner: httpx.BaseTransport,
        budget: KISBrokerageCallBudget,
    ) -> None:
        self._inner = inner
        self._budget = budget

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self._budget.reserve_brokerage()
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


class KISMockBrokerageHttpClient:
    """mock origin과 7개 endpoint/TR pair만 허용하며 모든 operation은 retry 0이다."""

    def __init__(
        self,
        *,
        settings: KISSettings,
        budget: KISBrokerageCallBudget,
        account_number: SecretStr | None = None,
        transport: httpx.BaseTransport | None = None,
        rate_limiter: RateLimiter | None = None,
        token_provider: Callable[[], str] | None = None,
    ) -> None:
        if settings.mode != "mock":
            raise KISMockLiveOrderGateClosed("KIS live brokerage allowlist is empty")
        if not settings.offline and any(
            value is not None for value in (account_number, transport, rate_limiter, token_provider)
        ):
            raise ValueError("KIS online private dependencies cannot be overridden")

        self._budget = budget
        self._token_issuer: _TokenIssuer | None = None
        self._redis_client: Any | None = None
        self._closed = False
        if settings.offline:
            if account_number is None:
                raise ValueError("offline KIS brokerage test account is required")
            selected_account = account_number
            inner = transport or httpx.HTTPTransport(verify=True, retries=0)
            limiter = rate_limiter or TokenBucket(
                rate_per_second=1 / settings.request_interval_seconds
            )
        else:
            try:
                selected_account = _KISMockBrokerageSecrets().kis_mock_account_no  # type: ignore[call-arg]
            except ValidationError:
                raise KISCredentialError("KIS mock brokerage account is unavailable") from None
            redis_client = _build_redis_client()
            token_issuer: _TokenIssuer | None = None
            try:
                scope = _provider_scope("mock")
                limiter = RedisIntervalLimiter(
                    redis_client,
                    key=f"kis:rest:v3:{scope}",
                    interval_seconds=settings.request_interval_seconds,
                    max_wait_seconds=float(settings.kis_rate_limit_max_wait_seconds),
                    io_budget_seconds=8.0,
                )
                token_limiter = RedisIntervalLimiter(
                    redis_client,
                    key="kis:tokenp:v3:deployment",
                    interval_seconds=1.0,
                    max_wait_seconds=float(settings.kis_rate_limit_max_wait_seconds),
                    io_budget_seconds=8.0,
                )
                token_issuer = _TokenIssuer(settings, rate_limiter=token_limiter)

                def budgeted_issue() -> dict[str, Any]:
                    budget.reserve_token_p()
                    return token_issuer.issue()

                token_manager = KISTokenManager(
                    mode="mock",
                    offline=False,
                    redis_client=redis_client,
                    issuer=budgeted_issue,
                    scope=scope,
                )
                token_provider = token_manager.get_access_token
                inner = httpx.HTTPTransport(verify=True, retries=0)
            except Exception:
                if token_issuer is not None:
                    token_issuer.close()
                redis_client.close()
                raise
            self._token_issuer = token_issuer
            self._redis_client = redis_client

        account_text = selected_account.get_secret_value()
        if _ACCOUNT_PATTERN.fullmatch(account_text) is None:
            self._close_private_dependencies()
            raise KISCredentialError("KIS mock brokerage account is unavailable")
        self._cano, self._product_code = account_text.split("-", maxsplit=1)
        account_text = ""
        budgeted_transport: _BrokerageBudgetTransport | None = None
        credential_transport: _CredentialTransport | None = None
        try:
            credential_transport = _CredentialTransport(
                inner,
                settings=settings,
                token_provider=token_provider,
                rate_limiter=limiter,
                max_response_bytes=_MAX_RESPONSE_BYTES,
                max_json_depth=32,
                sensitive_values=lambda: (self._cano,),
            )
            budgeted_transport = _BrokerageBudgetTransport(credential_transport, budget)
            self._http = httpx.Client(
                transport=budgeted_transport,
                timeout=settings.kis_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            )
        except Exception:
            if budgeted_transport is not None:
                budgeted_transport.close()
            elif credential_transport is not None:
                credential_transport.close()
            else:
                inner.close()
            self._close_private_dependencies()
            self._clear_account()
            raise
        self._origin = settings.base_url

    def request(
        self,
        method: str,
        path: str,
        tr_id: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """caller가 origin/header/account/TR을 주입하지 못하는 exact mock allowlist 경계다."""
        normalized_method = method.upper()
        if (normalized_method, path, tr_id) not in _APPROVED_OPERATIONS:
            raise ValueError("KIS mock brokerage endpoint/TR allowlist rejected the request")
        if normalized_method == "POST":
            if params is not None or json_body is None:
                raise ValueError("KIS mock brokerage POST shape is invalid")
            expected = _POST_FIELDS[(path, tr_id)]
            fields = set(json_body)
            if path == ORDER_CASH_PATH:
                allowed = expected | {"EXCG_ID_DVSN_CD"}
                if fields != expected and fields != allowed:
                    raise ValueError(
                        "KIS mock brokerage POST field allowlist rejected the request"
                    )
            elif fields != expected:
                raise ValueError("KIS mock brokerage POST field allowlist rejected the request")
            body = dict(json_body)
            if path == ORDER_CASH_PATH:
                exchange_division = body.get("EXCG_ID_DVSN_CD", "KRX")
                if _EXCHANGE_DIVISION.fullmatch(exchange_division) is None:
                    raise ValueError("KIS mock brokerage exchange division is invalid")
                if exchange_division != "KRX":
                    # KIS Developers order-cash 문서는 모의투자 현금주문 거래소를 KRX로 제한한다.
                    # 취소/체결조회 reference에는 exchange field를 보존하되, mock 신규 주문은
                    # provider reject를 만들기 전에 fail-closed 한다.
                    raise ValueError("KIS mock cash order supports KRX only")
                body["EXCG_ID_DVSN_CD"] = exchange_division
                body["SLL_TYPE"] = "01" if tr_id == MOCK_SELL_TR_ID else ""
                body["CNDT_PRIC"] = ""
            elif (
                "EXCG_ID_DVSN_CD" in body
                and _EXCHANGE_DIVISION.fullmatch(body["EXCG_ID_DVSN_CD"]) is None
            ):
                raise ValueError("KIS mock brokerage exchange division is invalid")
            body["CANO"] = self._cano
            body["ACNT_PRDT_CD"] = self._product_code
            query = None
        else:
            if json_body is not None or params is None:
                raise ValueError("KIS mock brokerage GET shape is invalid")
            expected = _GET_FIELDS[(path, tr_id)]
            if set(params) != expected:
                raise ValueError("KIS mock brokerage GET field allowlist rejected the request")
            query = dict(params)
            query["CANO"] = self._cano
            query["ACNT_PRDT_CD"] = self._product_code
            body = None
        brokerage_before = self._budget.counts["brokerage"]
        try:
            response = self._http.request(
                normalized_method,
                f"{self._origin}{path}",
                headers={_INTERNAL_TR_ID_HEADER: tr_id},
                params=query,
                json=body,
            )
        except (
            KISBrokerageCallBudgetExceeded,
        ):
            raise
        except KISResponseTooLargeError:
            raise KISMockBrokerageError(
                KISMockFailureReason.RESPONSE_TOO_LARGE
            ) from None
        except (KISRateLimitUnavailable, KISRateLimitWaitExceeded):
            raise KISMockBrokerageError(
                KISMockFailureReason.RATE_LIMIT_UNAVAILABLE
            ) from None
        except KISTokenCacheError:
            raise KISMockBrokerageError(
                KISMockFailureReason.CREDENTIAL_UNAVAILABLE
            ) from None
        except KISCredentialError:
            reason = (
                KISMockFailureReason.RESPONSE_SANITIZATION_FAILED
                if self._budget.counts["brokerage"] > brokerage_before
                else KISMockFailureReason.CREDENTIAL_UNAVAILABLE
            )
            raise KISMockBrokerageError(reason) from None
        except KISCallBudgetExceeded:
            raise KISBrokerageCallBudgetExceeded(
                "KIS brokerage physical reservation cap exhausted"
            ) from None
        except Exception:
            raise KISMockBrokerageError(
                KISMockFailureReason.TRANSPORT_UNAVAILABLE
            ) from None
        finally:
            if body is not None:
                body.clear()
            if query is not None:
                query.clear()
        if response.status_code >= 400:
            raise KISMockBrokerageError(
                KISMockFailureReason.HTTP_ERROR,
                http_status=response.status_code,
            )
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise KISMockBrokerageError(KISMockFailureReason.RESPONSE_TOO_LARGE)
        try:
            raw: object = response.json()
        except ValueError:
            raise KISMockBrokerageError(
                KISMockFailureReason.RESPONSE_INVALID
            ) from None
        if not isinstance(raw, dict):
            raise KISMockBrokerageError(KISMockFailureReason.RESPONSE_INVALID)
        payload = cast(dict[str, Any], raw)
        if payload.get("rt_cd") not in ("0", 0):
            provider_code = payload.get("msg_cd")
            raise KISMockBrokerageError(
                KISMockFailureReason.PROVIDER_REJECTED,
                provider_code=provider_code if isinstance(provider_code, str) else None,
            )
        return _sanitize_payload(payload)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._http.close()
        finally:
            self._close_private_dependencies()
            self._clear_account()

    def _close_private_dependencies(self) -> None:
        try:
            if self._token_issuer is not None:
                self._token_issuer.close()
        finally:
            if self._redis_client is not None:
                self._redis_client.close()

    def _clear_account(self) -> None:
        self._cano = ""
        self._product_code = ""


def _sanitize_payload(value: dict[str, Any]) -> dict[str, Any]:
    sanitized = _sanitize_value(value)
    if not isinstance(sanitized, dict):
        raise KISMockBrokerageError(KISMockFailureReason.RESPONSE_INVALID)
    return cast(dict[str, Any], sanitized)


def _sanitize_value(value: object, *, depth: int = 0) -> object:
    if depth > 32:
        raise KISMockBrokerageError(KISMockFailureReason.RESPONSE_TOO_LARGE)
    if isinstance(value, dict):
        return {
            str(key): _sanitize_value(child, depth=depth + 1)
            for key, child in value.items()
            if _normalized(str(key))
            not in {
                "cano",
                "acntprdtcd",
                "account",
                "accountno",
                "appkey",
                "appsecret",
                "authorization",
                "token",
            }
        }
    if isinstance(value, list):
        if len(value) > 1_000:
            raise KISMockBrokerageError(KISMockFailureReason.RESPONSE_TOO_LARGE)
        return [_sanitize_value(child, depth=depth + 1) for child in value]
    if isinstance(value, str) and len(value) > 8_192:
        raise KISMockBrokerageError(KISMockFailureReason.RESPONSE_TOO_LARGE)
    return value


def _bounded_provider_code(value: str | None) -> str | None:
    """provider 자유서술 대신 짧은 공식 code 형태만 diagnostic으로 통과시킨다."""
    if value is None or _PROVIDER_CODE_PATTERN.fullmatch(value) is None:
        return None
    return value


def _normalized(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


__all__ = [
    "KISBrokerageCallBudget",
    "KISMockBrokerageError",
    "KISMockBrokerageHttpClient",
    "KISMockFailureReason",
    "KISSettings",
    "TokenBucket",
]
