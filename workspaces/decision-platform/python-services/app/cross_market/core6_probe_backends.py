"""S4.8 Core 6 packet executor의 KIS·SEC EDGAR·KRX provider-specific backend다.

각 backend는 `core6_probe`가 claim한 packet만 소비하며 raw provider body/header/query를 DB, file,
receipt 또는 log에 보관하지 않는다. KIS는 cached OAuth token만 허용해 tokenP를 새로 열지 않고,
SEC EDGAR와 KRX도 고정 origin/endpoint와 retry 0의 existing hardened transport를 사용한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final, Protocol
from uuid import uuid4

from app.cross_market.core6_probe import (
    Core6ProbeBackend,
    Core6ProbeBackendResult,
    Core6ProbeError,
    Core6ProbePacket,
    core6_request_plan_digest,
)
from app.data._shared.canonical_json import canonical_json_sha256
from app.data.kis.accounting import (
    CollectionRunRecorder,
    CollectionRunStatus,
    LogicalOperation,
    PhysicalChannel,
)
from app.data.kis.calendar import is_xkrx_trading_day
from app.data.kis.http_client import KISHttpClient, KISHttpError
from app.data.kis.market_client import KISMarketClient
from app.data.kis.parsers import CurrentPrice
from app.data.kis.settings import KISSettings
from app.data.krx.client import KrxHttpError, KrxOpenApiClient
from app.data.krx.parsers import KrxDailyRow
from app.data.krx.settings import KrxOpenApiSettings
from app.rag.oa112_downloader import (
    Oa112DownloadError,
    _Oa112SourceDeadline,
    _SocketOa112DnsResolver,
    _StdlibOa112HttpsTransport,
    _resolve_public_addresses,
    _validate_peer,
)


_SEC_HOSTNAME: Final[str] = "data.sec.gov"
_SEC_MAX_RESPONSE_BYTES: Final[int] = 512 * 1024
_SEC_TIMEOUT_SECONDS: Final[float] = 10.0
_SEC_USER_AGENT_MAX_CHARS: Final[int] = 256
_SEC_OPERATIONS: Final[frozenset[str]] = frozenset(
    {"SEC_EDGAR_SUBMISSIONS", "SEC_EDGAR_COMPANYFACTS"}
)
_KIS_OPERATION: Final[str] = "KIS_CURRENT_PRICE"
_KRX_SERVICE_BY_OPERATION: Final[dict[str, str]] = {
    "KRX_KOSPI_DAILY": "stk_bydd_trd",
    "KRX_KOSDAQ_DAILY": "ksq_bydd_trd",
}


class _KisProbeSession(Protocol):
    """KIS cached-token preflight와 one market-data attempt를 분리하는 private runtime session이다."""

    def bind_packet_expiry(self, *, expires_at: datetime) -> None: ...

    def preflight(self) -> None: ...

    def current_price(self, *, symbol: str) -> CurrentPrice: ...

    def physical_call_count(self) -> int: ...

    def close(self) -> None: ...


class _KrxProbeSession(Protocol):
    """KRX client lifetime을 one packet에 묶어 Redis quota/client cleanup을 보장한다."""

    def bind_packet_expiry(self, *, expires_at: datetime) -> None: ...

    def preflight(self, *, as_of: date) -> None: ...

    def fetch_rows(self, *, as_of: date, service: str) -> tuple[KrxDailyRow, ...]: ...

    def physical_call_count(self) -> int: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SecEdgarProbeHttpResponse:
    """SEC raw body를 executor 밖으로 retained state 없이 전달하는 bounded transient response다."""

    status_code: int
    body: bytes
    physical_call_count: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.status_code, int)
            or not 100 <= self.status_code <= 599
            or not isinstance(self.body, bytes)
            or len(self.body) > _SEC_MAX_RESPONSE_BYTES
            or self.physical_call_count != 1
        ):
            raise Core6ProbeError("CORE6_PROBE_SEC_RESPONSE_INVALID")


class SecEdgarProbeTransport(Protocol):
    """Fixed SEC origin만 허용하는 one-request transport contract다."""

    def get(
        self,
        *,
        operation: str,
        resource_id: str,
        user_agent: str,
        expires_at: datetime,
    ) -> SecEdgarProbeHttpResponse: ...


class Core6KisCurrentPriceBackend(Core6ProbeBackend):
    """Cached KIS token으로 current-price endpoint 한 번만 호출하는 direct-read backend다."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], _KisProbeSession] | None = None,
    ) -> None:
        self._session_factory = session_factory or _ProductionKisProbeSession
        self._session: _KisProbeSession | None = None

    def preflight(self, *, packet: Core6ProbePacket) -> None:
        """Packet consume 전에 KIS mode, cached token, one-data-call cap을 local-only로 확인한다."""

        if packet.operation != _KIS_OPERATION:
            raise Core6ProbeError("CORE6_PROBE_KIS_OPERATION_INVALID")
        session = self._session_factory()
        try:
            session.bind_packet_expiry(expires_at=packet.expires_at)
            session.preflight()
        except Core6ProbeError:
            _close_quietly(session)
            raise
        except Exception as error:
            _close_quietly(session)
            raise Core6ProbeError("CORE6_PROBE_KIS_PREFLIGHT_UNAVAILABLE") from error
        self._session = session

    def execute(self, *, packet: Core6ProbePacket) -> Core6ProbeBackendResult:
        """Stored token이 유지되는 경우에만 KIS current price one data handoff를 수행한다."""

        session = self._take_session()
        try:
            current = session.current_price(symbol=packet.resource_id)
            calls = session.physical_call_count()
            if calls != 1:
                return _not_executed_or_protocol_failure(calls)
            return Core6ProbeBackendResult(
                outcome="SUCCESS",
                provider_status_class="HTTP_2XX",
                projection_hash=_kis_projection_hash(current),
                physical_call_count=1,
            )
        except Exception as error:
            failed_calls = _safe_session_call_count(session)
            if failed_calls == 0:
                return _not_executed_or_protocol_failure(failed_calls)
            return Core6ProbeBackendResult(
                outcome="FAILED",
                provider_status_class=_kis_status_class(error),
                projection_hash=None,
                physical_call_count=1,
            )
        finally:
            _close_quietly(session)

    def _take_session(self) -> _KisProbeSession:
        session = self._session
        self._session = None
        if session is None:
            raise Core6ProbeError("CORE6_PROBE_KIS_PREFLIGHT_REQUIRED")
        return session


class Core6KrxDailyBackend(Core6ProbeBackend):
    """Existing KRX private credential/quota transport를 reuse하는 one-service backend다."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], _KrxProbeSession] | None = None,
    ) -> None:
        self._session_factory = session_factory or _ProductionKrxProbeSession
        self._session: _KrxProbeSession | None = None

    def preflight(self, *, packet: Core6ProbePacket) -> None:
        """KOSPI/KOSDAQ fixed service와 completed XKRX date를 socket 전에 확인한다."""

        if packet.operation not in _KRX_SERVICE_BY_OPERATION:
            raise Core6ProbeError("CORE6_PROBE_KRX_OPERATION_INVALID")
        as_of = _packet_date(packet)
        session = self._session_factory()
        try:
            session.bind_packet_expiry(expires_at=packet.expires_at)
            session.preflight(as_of=as_of)
        except Core6ProbeError:
            _close_quietly(session)
            raise
        except Exception as error:
            _close_quietly(session)
            raise Core6ProbeError("CORE6_PROBE_KRX_PREFLIGHT_UNAVAILABLE") from error
        self._session = session

    def execute(self, *, packet: Core6ProbePacket) -> Core6ProbeBackendResult:
        """One fixed KRX service만 strict parse하고 derived projection hash만 반환한다."""

        session = self._take_session()
        try:
            rows = session.fetch_rows(
                as_of=_packet_date(packet),
                service=_KRX_SERVICE_BY_OPERATION[packet.operation],
            )
            calls = session.physical_call_count()
            if calls != 1:
                return _not_executed_or_protocol_failure(calls)
            return Core6ProbeBackendResult(
                outcome="SUCCESS",
                provider_status_class="HTTP_2XX",
                projection_hash=_krx_projection_hash(rows),
                physical_call_count=1,
            )
        except Exception as error:
            failed_calls = _safe_session_call_count(session)
            if failed_calls == 0:
                return _not_executed_or_protocol_failure(failed_calls)
            return Core6ProbeBackendResult(
                outcome="FAILED",
                provider_status_class=_krx_status_class(error),
                projection_hash=None,
                physical_call_count=1,
            )
        finally:
            _close_quietly(session)

    def _take_session(self) -> _KrxProbeSession:
        session = self._session
        self._session = None
        if session is None:
            raise Core6ProbeError("CORE6_PROBE_KRX_PREFLIGHT_REQUIRED")
        return session


class Core6SecEdgarBackend(Core6ProbeBackend):
    """SEC author-owned JSON endpoint를 fixed User-Agent와 one transient parse로 읽는 backend다."""

    def __init__(
        self,
        *,
        transport: SecEdgarProbeTransport | None = None,
        user_agent_reader: Callable[[], str] | None = None,
    ) -> None:
        self._transport = transport or StdlibSecEdgarProbeTransport()
        self._user_agent_reader = user_agent_reader or (lambda: os.environ.get("SEC_EDGAR_USER_AGENT", ""))
        self._user_agent = ""

    def preflight(self, *, packet: Core6ProbePacket) -> None:
        """SEC packet과 operator-owned contact User-Agent를 provider socket 전에 fail-closed한다."""

        if packet.operation not in _SEC_OPERATIONS:
            raise Core6ProbeError("CORE6_PROBE_SEC_OPERATION_INVALID")
        user_agent = self._user_agent_reader()
        if not _is_safe_sec_user_agent(user_agent):
            raise Core6ProbeError("CORE6_PROBE_SEC_USER_AGENT_REQUIRED")
        self._user_agent = user_agent

    def execute(self, *, packet: Core6ProbePacket) -> Core6ProbeBackendResult:
        """HTTP status와 minimal JSON/CIK shape만 validate하고 provider body는 즉시 폐기한다."""

        user_agent = self._user_agent
        self._user_agent = ""
        if not user_agent:
            raise Core6ProbeError("CORE6_PROBE_SEC_PREFLIGHT_REQUIRED")
        try:
            response = self._transport.get(
                operation=packet.operation,
                resource_id=packet.resource_id,
                user_agent=user_agent,
                expires_at=packet.expires_at,
            )
        except Core6ProbeError as error:
            return _not_executed_or_transport_failure(error.physical_call_count)
        except Exception:
            # transport protocol이 handoff 뒤 예외를 누락해도 physical attempt를 과소 계상하지 않는다.
            return _not_executed_or_transport_failure(1)
        if 200 <= response.status_code <= 299:
            if not _sec_body_matches_cik(body=response.body, resource_id=packet.resource_id):
                return Core6ProbeBackendResult(
                    outcome="FAILED",
                    provider_status_class="PROTOCOL",
                    projection_hash=None,
                    physical_call_count=1,
                )
            return Core6ProbeBackendResult(
                outcome="SUCCESS",
                provider_status_class="HTTP_2XX",
                projection_hash=_sec_parser_projection_hash(packet),
                physical_call_count=1,
            )
        return Core6ProbeBackendResult(
            outcome="FAILED",
            provider_status_class=_http_status_class(response.status_code),
            projection_hash=None,
            physical_call_count=1,
        )


class _ProductionKisProbeSession:
    """KIS private client를 cache-only token mode와 exact one market-data cap으로 조립한다."""

    def __init__(self) -> None:
        # 현재 환경의 offline kill switch를 존중한다. packet이 있어도 이를 우회해 live를 열지 않는다.
        settings = KISSettings(kis_retry_attempts=1)
        if settings.offline:
            raise Core6ProbeError("CORE6_PROBE_KIS_OFFLINE")
        self._settings = settings
        self._recorder = CollectionRunRecorder(
            run_id=uuid4(),
            started_at=datetime.now(UTC),
            logical_caps={
                LogicalOperation.CURRENT_PRICE: 1,
                LogicalOperation.DAILY_BARS: 0,
                LogicalOperation.HOLIDAY: 0,
            },
            physical_caps={
                PhysicalChannel.MARKET_DATA: 1,
                PhysicalChannel.TOKEN_P: 0,
            },
        )
        self._packet_expires_at: datetime | None = None
        self._client = KISHttpClient(
            settings,
            accounting=self._recorder,
            require_cached_token=True,
            deadline_guard=self._require_packet_current,
        )
        self._market = KISMarketClient(settings, self._client, accounting=self._recorder)
        self._completed = False

    def preflight(self) -> None:
        self._client.require_cached_access_token()

    def bind_packet_expiry(self, *, expires_at: datetime) -> None:
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise Core6ProbeError("CORE6_PROBE_PACKET_EXPIRY_INVALID")
        self._packet_expires_at = expires_at.astimezone(UTC)

    def current_price(self, *, symbol: str) -> CurrentPrice:
        try:
            return self._market.current_price(symbol)
        finally:
            self._completed = True

    def physical_call_count(self) -> int:
        status = CollectionRunStatus.SUCCESS if self._completed else CollectionRunStatus.FAILED
        summary = self._recorder.snapshot(completed_at=datetime.now(UTC), status=status)
        return next(
            item.attempts
            for item in summary.physical_attempts
            if item.channel == PhysicalChannel.MARKET_DATA
        )

    def close(self) -> None:
        self._client.close()

    def _require_packet_current(self) -> None:
        """KIS limiter/token cache 뒤에도 exact packet window가 끝나면 socket 전에 종료한다."""

        expires_at = self._packet_expires_at
        if expires_at is None or datetime.now(UTC) >= expires_at:
            raise Core6ProbeError("CORE6_PROBE_PACKET_EXPIRED")


class _ProductionKrxProbeSession:
    """KRX service probe가 기존 private credential transport와 quota reservation을 그대로 재사용한다."""

    def __init__(self) -> None:
        self._packet_expires_at: datetime | None = None
        self._client = KrxOpenApiClient(
            KrxOpenApiSettings(
                max_calls_per_run=1,
                max_attempts_per_request=1,
                logical_deadline_seconds=130.0,
            )
        )

    def preflight(self, *, as_of: date) -> None:
        if not is_xkrx_trading_day(as_of):
            raise Core6ProbeError("CORE6_PROBE_KRX_DATE_INVALID")

    def bind_packet_expiry(self, *, expires_at: datetime) -> None:
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise Core6ProbeError("CORE6_PROBE_PACKET_EXPIRY_INVALID")
        self._packet_expires_at = expires_at.astimezone(UTC)

    def fetch_rows(self, *, as_of: date, service: str) -> tuple[KrxDailyRow, ...]:
        return self._client.fetch_service_rows(
            as_of,
            service=service,
            deadline_monotonic=self._packet_deadline_monotonic(),
        )

    def physical_call_count(self) -> int:
        return self._client.physical_attempt_count

    def close(self) -> None:
        self._client.close()

    def _packet_deadline_monotonic(self) -> float:
        """wall-clock packet expiry를 KRX transport가 반복 확인하는 monotonic deadline으로 축소한다."""

        expires_at = self._packet_expires_at
        if expires_at is None:
            raise Core6ProbeError("CORE6_PROBE_PACKET_EXPIRY_UNBOUND")
        remaining = (expires_at - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            raise Core6ProbeError("CORE6_PROBE_PACKET_EXPIRED")
        return time.monotonic() + remaining


class StdlibSecEdgarProbeTransport:
    """OA112 hardened DNS/TLS primitive을 reuse해 SEC JSON endpoint 한 번만 요청한다."""

    def __init__(self) -> None:
        self._resolver = _SocketOa112DnsResolver()
        self._transport = _StdlibOa112HttpsTransport()

    def get(
        self,
        *,
        operation: str,
        resource_id: str,
        user_agent: str,
        expires_at: datetime,
    ) -> SecEdgarProbeHttpResponse:
        """Redirect/encoding/MIME drift를 reject하고 bounded body를 memory에서만 반환한다."""

        target = _sec_target(operation=operation, resource_id=resource_id)
        sent = False
        try:
            with _Oa112SourceDeadline(expires_at=expires_at) as deadline:
                first_addresses = _resolve_public_addresses(
                    _SEC_HOSTNAME,
                    resolver=self._resolver,
                    deadline=deadline,
                )
                second_addresses = _resolve_public_addresses(
                    _SEC_HOSTNAME,
                    resolver=self._resolver,
                    deadline=deadline,
                )
                if set(first_addresses) != set(second_addresses):
                    raise Core6ProbeError("CORE6_PROBE_SEC_DNS_REBINDING")
                with self._transport.connect(
                    hostname=_SEC_HOSTNAME,
                    pinned_ip=first_addresses[0],
                    connect_timeout_seconds=min(_SEC_TIMEOUT_SECONDS, deadline.remaining_seconds()),
                    read_timeout_seconds=min(_SEC_TIMEOUT_SECONDS, deadline.remaining_seconds()),
                    deadline=deadline,
                ) as connection:
                    _validate_peer(connection.peer_ip, first_addresses)
                    # `get` 내부의 send/read 실패 지점은 전송 여부를 신뢰성 있게 구분할 수 없다.
                    # one-shot cap을 과소 계상하지 않도록 provider handoff 직전에 물리 호출로 보수 계상한다.
                    sent = True
                    response = connection.get(
                        target=target,
                        headers={
                            "Accept": "application/json",
                            "Accept-Encoding": "identity",
                            "Connection": "close",
                            "Host": _SEC_HOSTNAME,
                            "User-Agent": user_agent,
                        },
                        read_timeout_seconds=min(_SEC_TIMEOUT_SECONDS, deadline.remaining_seconds()),
                    )
                    _validate_sec_response_headers(response.headers)
                    body = _read_sec_body(
                        response=response,
                        deadline=deadline,
                    )
                    return SecEdgarProbeHttpResponse(
                        status_code=response.status_code,
                        body=body,
                        physical_call_count=1,
                    )
        except Core6ProbeError:
            raise
        except (Oa112DownloadError, OSError, TimeoutError) as error:
            raise Core6ProbeError(
                "CORE6_PROBE_SEC_TRANSPORT_UNAVAILABLE",
                physical_call_count=1 if sent else 0,
            ) from error


def build_core6_backend(*, operation: str) -> Core6ProbeBackend:
    """CLI는 user input 대신 fixed operation map으로만 provider backend를 선택한다."""

    if operation == _KIS_OPERATION:
        return Core6KisCurrentPriceBackend()
    if operation in _SEC_OPERATIONS:
        return Core6SecEdgarBackend()
    if operation in _KRX_SERVICE_BY_OPERATION:
        return Core6KrxDailyBackend()
    raise Core6ProbeError("CORE6_PROBE_OPERATION_PROVIDER_INVALID")


def _packet_date(packet: Core6ProbePacket) -> date:
    try:
        return date.fromisoformat(packet.date)
    except ValueError as error:
        raise Core6ProbeError("CORE6_PROBE_KRX_DATE_INVALID") from error


def _kis_projection_hash(current: CurrentPrice) -> str:
    """KIS normalized scalar만 hash하고 provider field names/body는 durable state에 남기지 않는다."""

    return canonical_json_sha256(
        {
            "high": current.high,
            "low": current.low,
            "open": current.open,
            "previousDiff": current.previous_diff,
            "previousRate": str(current.previous_rate),
            "price": current.price,
            "symbol": current.symbol,
            "turnover": current.turnover,
            "volume": current.volume,
        }
    )


def _krx_projection_hash(rows: Sequence[KrxDailyRow]) -> str:
    """Strict parser가 만든 KRX scalar projection만 canonical hash로 남긴다."""

    if not rows:
        raise Core6ProbeError("CORE6_PROBE_KRX_ROWS_INVALID", physical_call_count=1)
    return canonical_json_sha256(
        [
            {
                "asOfDate": row.as_of_date.isoformat(),
                "market": row.market,
                "marketCap": row.market_cap,
                "symbol": row.symbol,
                "tradingValue": row.trading_value,
            }
            for row in sorted(rows, key=lambda value: value.symbol)
        ]
    )


def _sec_parser_projection_hash(packet: Core6ProbePacket) -> str:
    """SEC body를 retained hash input으로 쓰지 않고 parser success proof만 bind한다."""

    return hashlib.sha256(
        json.dumps(
            {
                "operation": packet.operation,
                "parserOutcome": "VALIDATED_TRANSIENT_ONLY",
                "providerFamily": packet.provider_family,
                "resourceId": packet.resource_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _kis_status_class(error: Exception) -> str:
    if isinstance(error, KISHttpError):
        return _http_status_class(error.status_code)
    if type(error).__name__ in {"KISResponseError", "KISResponseTooLargeError"}:
        return "PROTOCOL"
    return "TRANSPORT"


def _krx_status_class(error: Exception) -> str:
    if isinstance(error, KrxHttpError) and error.status_code is not None:
        return _http_status_class(error.status_code)
    if isinstance(error, KrxHttpError):
        return "PROTOCOL"
    return "TRANSPORT"


def _http_status_class(status_code: int) -> str:
    if 400 <= status_code <= 499:
        return "HTTP_4XX"
    if 500 <= status_code <= 599:
        return "HTTP_5XX"
    return "PROTOCOL"


def _safe_session_call_count(session: _KisProbeSession | _KrxProbeSession) -> int | None:
    try:
        count = session.physical_call_count()
    except Exception:
        # Provider handoff 뒤 recorder 자체가 깨진 경우에는 0이라고 단정할 수 없다.
        return None
    return count if count in {0, 1} else 1


def _not_executed_or_protocol_failure(calls: int) -> Core6ProbeBackendResult:
    if calls == 0:
        return Core6ProbeBackendResult(
            outcome="NOT_EXECUTED",
            provider_status_class="NOT_ATTEMPTED",
            projection_hash=None,
            physical_call_count=0,
        )
    return Core6ProbeBackendResult(
        outcome="FAILED",
        provider_status_class="PROTOCOL",
        projection_hash=None,
        physical_call_count=1,
    )


def _not_executed_or_transport_failure(calls: int) -> Core6ProbeBackendResult:
    if calls == 0:
        return _not_executed_or_protocol_failure(calls)
    return Core6ProbeBackendResult(
        outcome="FAILED",
        provider_status_class="TRANSPORT",
        projection_hash=None,
        physical_call_count=1,
    )


def _close_quietly(session: _KisProbeSession | _KrxProbeSession) -> None:
    try:
        session.close()
    except Exception:
        pass


def _is_safe_sec_user_agent(value: object) -> bool:
    return (
        isinstance(value, str)
        and 3 <= len(value) <= _SEC_USER_AGENT_MAX_CHARS
        and "\r" not in value
        and "\n" not in value
        and all(ord(character) >= 32 for character in value)
        and "@" in value
    )


def _sec_target(*, operation: str, resource_id: str) -> str:
    core6_request_plan_digest(operation=operation, resource_id=resource_id, date="NONE")
    cik = resource_id.removeprefix("CIK")
    if operation == "SEC_EDGAR_SUBMISSIONS":
        return f"/submissions/CIK{cik}.json"
    if operation == "SEC_EDGAR_COMPANYFACTS":
        return f"/api/xbrl/companyfacts/CIK{cik}.json"
    raise Core6ProbeError("CORE6_PROBE_SEC_OPERATION_INVALID")


def _validate_sec_response_headers(headers: Mapping[str, str]) -> None:
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower().strip()
        if (
            not lowered
            or lowered in normalized
            or not isinstance(value, str)
            or "\r" in value
            or "\n" in value
        ):
            raise Core6ProbeError("CORE6_PROBE_SEC_RESPONSE_HEADER_INVALID", physical_call_count=1)
        normalized[lowered] = value.strip()
    if "location" in normalized:
        raise Core6ProbeError("CORE6_PROBE_SEC_REDIRECT_FORBIDDEN", physical_call_count=1)
    if normalized.get("content-encoding", "identity").lower() != "identity":
        raise Core6ProbeError("CORE6_PROBE_SEC_RESPONSE_ENCODING_FORBIDDEN", physical_call_count=1)
    if normalized.get("transfer-encoding", "").lower() not in {"", "chunked"}:
        raise Core6ProbeError("CORE6_PROBE_SEC_RESPONSE_TRANSFER_INVALID", physical_call_count=1)
    content_type = normalized.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
    if content_type != "application/json":
        raise Core6ProbeError("CORE6_PROBE_SEC_RESPONSE_MIME_INVALID", physical_call_count=1)
    length = normalized.get("content-length")
    if length is not None and (not length.isdecimal() or int(length) > _SEC_MAX_RESPONSE_BYTES):
        raise Core6ProbeError("CORE6_PROBE_SEC_RESPONSE_BOUND", physical_call_count=1)


def _read_sec_body(*, response: Any, deadline: Any) -> bytes:
    chunks: list[bytes] = []
    total = 0
    iterator = response.iter_raw(chunk_size=16 * 1024)
    while True:
        response.set_read_timeout_seconds(
            timeout_seconds=min(_SEC_TIMEOUT_SECONDS, deadline.remaining_seconds())
        )
        try:
            chunk = next(iterator)
        except StopIteration:
            break
        deadline.remaining_seconds()
        if not isinstance(chunk, bytes) or not chunk:
            raise Core6ProbeError("CORE6_PROBE_SEC_RESPONSE_BODY_INVALID", physical_call_count=1)
        total += len(chunk)
        if total > _SEC_MAX_RESPONSE_BYTES:
            raise Core6ProbeError("CORE6_PROBE_SEC_RESPONSE_BOUND", physical_call_count=1)
        chunks.append(chunk)
    return b"".join(chunks)


def _sec_body_matches_cik(*, body: bytes, resource_id: str) -> bool:
    try:
        payload = json.loads(body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, Mapping):
        return False
    raw_cik = payload.get("cik")
    if isinstance(raw_cik, int) and not isinstance(raw_cik, bool):
        cik = f"{raw_cik:010d}"
    elif isinstance(raw_cik, str) and raw_cik.isdecimal():
        cik = raw_cik.zfill(10)
    else:
        return False
    return f"CIK{cik}" == resource_id
