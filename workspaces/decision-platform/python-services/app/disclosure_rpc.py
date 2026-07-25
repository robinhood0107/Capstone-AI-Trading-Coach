"""저장된 정제 공시 관측만 노출하는 S2.3 loopback gRPC 경계."""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from concurrent import futures
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hmac import compare_digest
from typing import Never, Protocol

import psycopg

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from app.data.opendart.models import DisclosureRiskEvent
from app.data.opendart.risk_mapping import load_default_risk_mapping
from app.data.opendart.scorer import score_disclosure_risk
from app.generated import disclosure_observation_pb2, disclosure_observation_pb2_grpc

_MAX_REQUEST_BYTES = 256 * 1024
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_CONCURRENCY = 8
_MAX_EVENTS = 100
_MAX_SOURCE_REFS = 100
_SOURCE_REF_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_EVENT_CODE_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_RECEIPT_NO_PATTERN = re.compile(r"^[0-9]{14}$")
_AUTH_METADATA_KEY = "x-decision-grpc-auth"


@dataclass(frozen=True)
class StoredDisclosureEvent:
    """DB projection에서 읽은 한 공시와 불투명 provenance를 변경 불가능한 값으로 운반한다."""

    symbol: str
    corp_code: str
    event_code: str
    receipt_no: str
    occurred_on: date
    observed_at: datetime
    mapping_version: str
    source_refs: tuple[str, ...]
    attributes: Mapping[str, str]


@dataclass(frozen=True)
class StoredDisclosureBatch:
    """같은 조회 window의 event·completeness·mapping 관측을 한 snapshot으로 묶는다."""

    symbol: str
    corp_code: str
    observed_at: datetime
    mapping_version: str
    complete: bool
    events: tuple[StoredDisclosureEvent, ...]
    source_refs: tuple[str, ...] = ()


class StoredDisclosureRepository(Protocol):
    """provider 호출 없이 PostgreSQL sanitized projection을 한 번 읽는 port다."""

    def load(
        self,
        *,
        symbol: str,
        corp_code: str | None,
        window_from: date,
        window_to: date,
        cancellation: QueryCancellation,
    ) -> StoredDisclosureBatch: ...


class LoopbackServerSettings(Protocol):
    """서버 factory가 비밀 설정 전체가 아니라 검증된 bind 주소만 보게 한다."""

    @property
    def bind_address(self) -> str: ...

    @property
    def shared_secret(self) -> str: ...


class CancellableDatabaseConnection(Protocol):
    """gRPC cancellation이 현재 DB 작업만 취소하도록 필요한 최소 connection 표면이다."""

    def cancel(self) -> None: ...


class QueryCancellation:
    """RPC lifecycle과 한 physical DB connection을 thread-safe하게 연결한다."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connection: CancellableDatabaseConnection | None = None
        self._cancelled = False

    def attach(self, connection: CancellableDatabaseConnection) -> None:
        """repository가 physical query 직전에 connection을 등록한다."""
        cancel_now = False
        with self._lock:
            if self._connection is not None:
                raise RuntimeError("database cancellation already has an active connection")
            self._connection = connection
            cancel_now = self._cancelled
        if cancel_now:
            _cancel_safely(connection)

    def raise_if_cancelled(self) -> None:
        """connection 획득 전후와 query 사이에서 이미 만료된 RPC가 새 DB 작업을 만들지 않게 한다."""
        with self._lock:
            cancelled = self._cancelled
        if cancelled:
            raise TimeoutError("stored disclosure query was cancelled")

    def detach(self, connection: CancellableDatabaseConnection) -> None:
        """query 종료 뒤 같은 connection만 해제해 다음 RPC resource와 섞이지 않게 한다."""
        with self._lock:
            if self._connection is connection:
                self._connection = None

    def cancel(self) -> None:
        """client cancellation callback에서 secret/error detail 없이 active query를 취소한다."""
        with self._lock:
            self._cancelled = True
            connection = self._connection
        if connection is not None:
            _cancel_safely(connection)


class DisclosureObservationServicer(
    disclosure_observation_pb2_grpc.DisclosureObservationServiceServicer
):
    """저장 관측을 bounded response로 변환하며 HTTP fallback과 재시도를 만들지 않는다."""

    def __init__(self, repository: StoredDisclosureRepository, shared_secret: str) -> None:
        self._repository = repository
        self._shared_secret = shared_secret

    def GetDisclosureEvents(
        self,
        request: disclosure_observation_pb2.GetDisclosureEventsRequest,
        context: grpc.ServicerContext,
    ) -> disclosure_observation_pb2.GetDisclosureEventsResponse:
        """검증된 symbol/window의 저장 관측만 반환하고 손상된 provenance는 fail-closed한다."""
        _require_authenticated(context, self._shared_secret)
        symbol, corp_code, as_of, window_from, window_to = _validate_request(
            request,
            context,
        )
        cancellation = QueryCancellation()
        context.add_callback(cancellation.cancel)
        try:
            batch = self._repository.load(
                symbol=symbol,
                corp_code=corp_code,
                window_from=window_from,
                window_to=window_to,
                cancellation=cancellation,
            )
        except Exception as error:
            code = _database_failure_status(error)
            _abort(context, code, _database_failure_detail(code))

        _validate_batch(
            batch,
            symbol=symbol,
            corp_code=corp_code,
            context=context,
        )
        mapping = load_default_risk_mapping()
        if batch.mapping_version != mapping.version:
            _abort(context, grpc.StatusCode.DATA_LOSS, "disclosure mapping version mismatch")

        risk_events = [
            DisclosureRiskEvent(
                symbol=event.symbol,
                corp_code=event.corp_code,
                event_code=event.event_code,
                receipt_no=event.receipt_no,
                occurred_on=event.occurred_on,
                attributes=dict(event.attributes),
            )
            for event in batch.events
        ]
        result = score_disclosure_risk(
            symbol,
            risk_events,
            as_of=as_of,
            mapping=mapping,
            window_days=max(1, (window_to - window_from).days),
        )
        source_refs = sorted(
            [
                *batch.source_refs,
                *(
                    source_ref
                    for event in batch.events
                    for source_ref in event.source_refs
                ),
            ]
        )
        response = disclosure_observation_pb2.GetDisclosureEventsResponse(
            symbol=symbol,
            corp_code=batch.corp_code,
            as_of=as_of.isoformat(),
            window_from=window_from.isoformat(),
            window_to=window_to.isoformat(),
            score=result.score,
            mapping_version=result.mapping_version,
            source_refs=source_refs,
            observed_at=_utc_text(batch.observed_at),
            complete=batch.complete,
        )
        response.events.extend(
            disclosure_observation_pb2.DisclosureRiskEvent(
                event_code=event.event_code,
                receipt_no=event.receipt_no,
                occurred_on=event.occurred_on.isoformat(),
            )
            for event in result.events
        )
        response.warnings.extend(
            disclosure_observation_pb2.DisclosureRiskWarning(
                code=warning.code,
                event_code=warning.event_code,
                receipt_no=warning.receipt_no,
                message=warning.message,
            )
            for warning in result.warnings
        )
        if response.ByteSize() > _MAX_RESPONSE_BYTES:
            _abort(context, grpc.StatusCode.DATA_LOSS, "disclosure response exceeds limit")
        return response


def create_disclosure_server(
    settings: LoopbackServerSettings,
    repository: StoredDisclosureRepository,
) -> grpc.Server:
    """검증된 loopback 주소에 health와 실제 business RPC만 등록하고 reflection은 제공하지 않는다."""
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=_MAX_CONCURRENCY),
        options=(
            ("grpc.max_receive_message_length", _MAX_REQUEST_BYTES),
            ("grpc.max_send_message_length", _MAX_RESPONSE_BYTES),
        ),
        maximum_concurrent_rpcs=_MAX_CONCURRENCY,
    )
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set(
        disclosure_observation_pb2.DESCRIPTOR.services_by_name[
            "DisclosureObservationService"
        ].full_name,
        health_pb2.HealthCheckResponse.SERVING,
    )
    disclosure_observation_pb2_grpc.add_DisclosureObservationServiceServicer_to_server(
        DisclosureObservationServicer(repository, settings.shared_secret),
        server,
    )  # type: ignore[no-untyped-call]
    bound_port = server.add_insecure_port(settings.bind_address)
    if bound_port == 0:
        raise RuntimeError("Python gRPC loopback port could not be bound")
    return server


def _require_authenticated(context: grpc.ServicerContext, shared_secret: str) -> None:
    values = [
        value
        for key, value in context.invocation_metadata()
        if key == _AUTH_METADATA_KEY
    ]
    value = values[0] if len(values) == 1 else None
    if not isinstance(value, str) or not compare_digest(value, shared_secret):
        _abort(
            context,
            grpc.StatusCode.UNAUTHENTICATED,
            "disclosure grpc authentication failed",
        )


def _validate_request(
    request: disclosure_observation_pb2.GetDisclosureEventsRequest,
    context: grpc.ServicerContext,
) -> tuple[str, str | None, date, date, date]:
    symbol = request.symbol
    corp_code = request.corp_code or None
    if not re.fullmatch(r"[0-9]{6}", symbol):
        _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "symbol is invalid")
    if corp_code is not None and not re.fullmatch(r"[0-9]{8}", corp_code):
        _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "corp_code is invalid")
    try:
        as_of = date.fromisoformat(request.as_of)
        window_from = date.fromisoformat(request.window_from)
        window_to = date.fromisoformat(request.window_to)
    except ValueError:
        _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "date field is invalid")
    if (
        window_from > window_to
        or window_to != as_of
        or (window_to - window_from).days > 365
    ):
        _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "disclosure window is invalid")
    return symbol, corp_code, as_of, window_from, window_to


def _validate_batch(
    batch: StoredDisclosureBatch,
    *,
    symbol: str,
    corp_code: str | None,
    context: grpc.ServicerContext,
) -> None:
    if (
        batch.symbol != symbol
        or (corp_code is not None and batch.corp_code != corp_code)
        or (batch.corp_code and re.fullmatch(r"[0-9]{8}", batch.corp_code) is None)
        or (batch.complete and not batch.corp_code)
    ):
        _abort(context, grpc.StatusCode.DATA_LOSS, "disclosure batch identity mismatch")
    if not 1 <= len(batch.mapping_version) <= 128:
        _abort(context, grpc.StatusCode.DATA_LOSS, "disclosure mapping version is invalid")
    if len(batch.events) > _MAX_EVENTS:
        _abort(context, grpc.StatusCode.DATA_LOSS, "disclosure event limit exceeded")
    if batch.observed_at.tzinfo is None:
        _abort(context, grpc.StatusCode.DATA_LOSS, "disclosure observation time is invalid")

    event_identities: set[tuple[object, ...]] = set()
    source_refs: set[str] = set()
    for source_ref in batch.source_refs:
        if (
            not _SOURCE_REF_PATTERN.fullmatch(source_ref)
            or source_ref in source_refs
        ):
            _abort(context, grpc.StatusCode.DATA_LOSS, "disclosure source reference is invalid")
        source_refs.add(source_ref)
    for event in batch.events:
        identity = (
            event.symbol,
            event.corp_code,
            event.event_code,
            event.receipt_no,
            event.occurred_on,
        )
        if identity in event_identities:
            _abort(context, grpc.StatusCode.DATA_LOSS, "duplicate disclosure event")
        event_identities.add(identity)
        if (
            event.symbol != batch.symbol
            or event.corp_code != batch.corp_code
            or event.mapping_version != batch.mapping_version
            or not _EVENT_CODE_PATTERN.fullmatch(event.event_code)
            or not _RECEIPT_NO_PATTERN.fullmatch(event.receipt_no)
            or event.observed_at.tzinfo is None
        ):
            _abort(context, grpc.StatusCode.DATA_LOSS, "disclosure event is malformed")
        for source_ref in event.source_refs:
            if (
                not _SOURCE_REF_PATTERN.fullmatch(source_ref)
                or source_ref in source_refs
            ):
                _abort(context, grpc.StatusCode.DATA_LOSS, "disclosure source reference is invalid")
            source_refs.add(source_ref)
    if len(source_refs) > _MAX_SOURCE_REFS:
        _abort(context, grpc.StatusCode.DATA_LOSS, "disclosure source reference limit exceeded")
    if batch.complete and not source_refs:
        _abort(context, grpc.StatusCode.DATA_LOSS, "complete disclosure batch lacks provenance")


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _abort(
    context: grpc.ServicerContext,
    code: grpc.StatusCode,
    detail: str,
) -> Never:
    context.abort(code, detail)
    raise AssertionError("gRPC abort returned unexpectedly")


def _cancel_safely(connection: CancellableDatabaseConnection) -> None:
    try:
        connection.cancel()
    except Exception:
        # 취소 실패 detail은 이미 종료 중인 RPC에 노출하지 않고 connection context가 close한다.
        return


def _database_failure_status(error: Exception) -> grpc.StatusCode:
    if isinstance(error, psycopg.Error):
        sqlstate = error.sqlstate or ""
        if sqlstate == "57014":
            return grpc.StatusCode.DEADLINE_EXCEEDED
        if sqlstate.startswith("28"):
            return grpc.StatusCode.UNAUTHENTICATED
        if sqlstate == "42501":
            return grpc.StatusCode.PERMISSION_DENIED
        if sqlstate.startswith("08") or (
            isinstance(error, psycopg.OperationalError) and not sqlstate
        ):
            return grpc.StatusCode.UNAVAILABLE
        if sqlstate.startswith(("21", "22", "23")):
            return grpc.StatusCode.DATA_LOSS
        return grpc.StatusCode.INTERNAL
    if isinstance(error, (ValueError, KeyError, TypeError)):
        return grpc.StatusCode.DATA_LOSS
    if isinstance(error, (TimeoutError, ConnectionError)):
        return grpc.StatusCode.UNAVAILABLE
    return grpc.StatusCode.INTERNAL


def _database_failure_detail(code: grpc.StatusCode) -> str:
    return {
        grpc.StatusCode.UNAVAILABLE: "stored disclosure database unavailable",
        grpc.StatusCode.DEADLINE_EXCEEDED: "stored disclosure database deadline exceeded",
        grpc.StatusCode.UNAUTHENTICATED: "stored disclosure database authentication failed",
        grpc.StatusCode.PERMISSION_DENIED: "stored disclosure database permission denied",
        grpc.StatusCode.DATA_LOSS: "stored disclosure database row is malformed",
        grpc.StatusCode.INTERNAL: "stored disclosure database invariant failed",
    }[code]
