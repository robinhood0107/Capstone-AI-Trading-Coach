from __future__ import annotations

import socket
import threading
import time
from concurrent import futures
from datetime import UTC, date, datetime

import grpc
import psycopg
import pytest
from grpc_reflection.v1alpha import reflection_pb2, reflection_pb2_grpc

from app.disclosure_rpc import (
    QueryCancellation,
    StoredDisclosureBatch,
    StoredDisclosureEvent,
    StoredDisclosureIncompleteError,
    StoredDisclosureOversizedError,
    create_disclosure_server,
)
from app.generated import (
    disclosure_observation_pb2,
    disclosure_observation_pb2_grpc,
)
from app.grpc_server import GrpcServerSettings

_SHARED_SECRET = "python-grpc-shared-secret-for-s2-3-tests-0001"
_AUTH_METADATA = (("x-decision-grpc-auth", _SHARED_SECRET),)


class FakeDisclosureRepository:
    def __init__(self, batch: StoredDisclosureBatch) -> None:
        self.batch = batch
        self.calls = 0

    def load(
        self,
        *,
        symbol: str,
        corp_code: str | None,
        window_from: date,
        window_to: date,
        cancellation: QueryCancellation,
    ) -> StoredDisclosureBatch:
        self.calls += 1
        return self.batch


def _loopback_address() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return f"127.0.0.1:{port}"


def _batch(event_count: int = 1) -> StoredDisclosureBatch:
    events = tuple(
        StoredDisclosureEvent(
            symbol="005930",
            corp_code="00126380",
            event_code="OPENDART:dfOcr",
            receipt_no=f"202607240000{index:02d}",
            occurred_on=date(2026, 7, 24),
            observed_at=datetime(2026, 7, 24, 1, 2, 3, tzinfo=UTC),
            mapping_version="s1.2-v1",
            source_refs=(f"{index + 1:064x}",),
            attributes={},
        )
        for index in range(event_count)
    )
    return StoredDisclosureBatch(
        symbol="005930",
        corp_code="00126380",
        observed_at=datetime(2026, 7, 24, 1, 2, 3, tzinfo=UTC),
        mapping_version="s1.2-v1",
        complete=True,
        events=events,
    )


def _request() -> disclosure_observation_pb2.GetDisclosureEventsRequest:
    return disclosure_observation_pb2.GetDisclosureEventsRequest(
        symbol="005930",
        corp_code="",
        as_of="2026-07-24",
        window_from="2025-07-24",
        window_to="2026-07-24",
    )


def _settings() -> GrpcServerSettings:
    return GrpcServerSettings(
        bind_address=_loopback_address(),
        shared_secret=_SHARED_SECRET,
    )


def _authorized_metadata() -> tuple[tuple[str, str], ...]:
    return _AUTH_METADATA


def _get_disclosure_events(
    stub: disclosure_observation_pb2_grpc.DisclosureObservationServiceStub,
    request: disclosure_observation_pb2.GetDisclosureEventsRequest,
    *,
    timeout: float,
) -> disclosure_observation_pb2.GetDisclosureEventsResponse:
    return stub.GetDisclosureEvents(
        request,
        timeout=timeout,
        metadata=_authorized_metadata(),
    )


def test_real_business_rpc_roundtrip_is_loopback_single_call_without_reflection() -> None:
    repository = FakeDisclosureRepository(_batch())
    settings = _settings()
    server = create_disclosure_server(settings, repository)
    server.start()
    channel = grpc.insecure_channel(settings.bind_address)
    try:
        response = _get_disclosure_events(
            disclosure_observation_pb2_grpc.DisclosureObservationServiceStub(channel),
            _request(),
            timeout=0.5,
        )

        assert repository.calls == 1
        assert response.symbol == "005930"
        assert response.corp_code == "00126380"
        assert response.mapping_version == "s1.2-v1"
        assert response.complete is True
        assert [item.event_code for item in response.events] == ["OPENDART:dfOcr"]
        assert list(response.source_refs) == [f"{1:064x}"]

        reflection_stub = reflection_pb2_grpc.ServerReflectionStub(channel)
        with pytest.raises(grpc.RpcError) as reflection_error:
            list(
                reflection_stub.ServerReflectionInfo(
                    iter(
                        [
                            reflection_pb2.ServerReflectionRequest(
                                list_services=""
                            )
                        ]
                    ),
                    timeout=0.5,
                )
            )
        assert reflection_error.value.code() == grpc.StatusCode.UNIMPLEMENTED
    finally:
        channel.close()
        server.stop(grace=0).wait(timeout=2)


def test_business_rpc_rejects_missing_shared_secret_before_repository_work() -> None:
    repository = FakeDisclosureRepository(_batch())
    settings = _settings()
    server = create_disclosure_server(settings, repository)
    server.start()
    channel = grpc.insecure_channel(settings.bind_address)
    try:
        stub = disclosure_observation_pb2_grpc.DisclosureObservationServiceStub(channel)
        with pytest.raises(grpc.RpcError) as missing:
            stub.GetDisclosureEvents(_request(), timeout=0.5)
        assert missing.value.code() == grpc.StatusCode.UNAUTHENTICATED

        with pytest.raises(grpc.RpcError) as wrong:
            stub.GetDisclosureEvents(
                _request(),
                timeout=0.5,
                metadata=(("x-decision-grpc-auth", "wrong-secret".ljust(32, "x")),),
            )
        assert wrong.value.code() == grpc.StatusCode.UNAUTHENTICATED
        assert repository.calls == 0
    finally:
        channel.close()
        server.stop(grace=0).wait(timeout=2)


def test_stopped_python_process_is_unavailable_without_retry() -> None:
    address = _loopback_address()
    channel = grpc.insecure_channel(address)
    try:
        stub = disclosure_observation_pb2_grpc.DisclosureObservationServiceStub(channel)
        with pytest.raises(grpc.RpcError) as error:
            stub.GetDisclosureEvents(_request(), timeout=0.1)
        assert error.value.code() in {
            grpc.StatusCode.UNAVAILABLE,
            grpc.StatusCode.DEADLINE_EXCEEDED,
        }
    finally:
        channel.close()


def test_request_and_response_bounds_fail_closed() -> None:
    repository = FakeDisclosureRepository(_batch(event_count=101))
    settings = _settings()
    server = create_disclosure_server(settings, repository)
    server.start()
    channel = grpc.insecure_channel(settings.bind_address)
    try:
        stub = disclosure_observation_pb2_grpc.DisclosureObservationServiceStub(channel)
        with pytest.raises(grpc.RpcError) as response_error:
            _get_disclosure_events(stub, _request(), timeout=0.5)
        assert response_error.value.code() == grpc.StatusCode.OUT_OF_RANGE
        assert repository.calls == 1

        oversized = _request()
        oversized.symbol = "9" * (256 * 1024)
        with pytest.raises(grpc.RpcError) as request_error:
            _get_disclosure_events(stub, oversized, timeout=0.5)
        assert request_error.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
        assert repository.calls == 1
    finally:
        channel.close()
        server.stop(grace=0).wait(timeout=2)


def test_incomplete_response_is_still_validated_and_malformed_payload_fails() -> None:
    malformed = StoredDisclosureBatch(
        symbol="005930",
        corp_code="",
        observed_at=datetime(2026, 7, 24, 1, 2, 3, tzinfo=UTC),
        mapping_version="",
        complete=False,
        events=(),
        source_refs=("not-a-sha256",),
    )
    repository = FakeDisclosureRepository(malformed)
    settings = _settings()
    server = create_disclosure_server(settings, repository)
    server.start()
    channel = grpc.insecure_channel(settings.bind_address)
    try:
        with pytest.raises(grpc.RpcError) as error:
            _get_disclosure_events(
                disclosure_observation_pb2_grpc.DisclosureObservationServiceStub(channel),
                _request(),
                timeout=0.5,
            )
        assert error.value.code() == grpc.StatusCode.DATA_LOSS
    finally:
        channel.close()
        server.stop(grace=0).wait(timeout=2)


class _SqlStateOperationalError(psycopg.OperationalError):
    def __init__(self, sqlstate: str) -> None:
        self._sqlstate = sqlstate
        super().__init__("sanitized database failure")

    @property
    def sqlstate(self) -> str:
        return self._sqlstate


class _FailingRepository:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def load(
        self,
        *,
        symbol: str,
        corp_code: str | None,
        window_from: date,
        window_to: date,
        cancellation: QueryCancellation,
    ) -> StoredDisclosureBatch:
        raise self.error


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_SqlStateOperationalError("08006"), grpc.StatusCode.UNAVAILABLE),
        (_SqlStateOperationalError("57014"), grpc.StatusCode.DEADLINE_EXCEEDED),
        (_SqlStateOperationalError("28P01"), grpc.StatusCode.UNAUTHENTICATED),
        (_SqlStateOperationalError("42501"), grpc.StatusCode.PERMISSION_DENIED),
        (StoredDisclosureOversizedError(), grpc.StatusCode.OUT_OF_RANGE),
        (StoredDisclosureIncompleteError(), grpc.StatusCode.FAILED_PRECONDITION),
        (TimeoutError(), grpc.StatusCode.DEADLINE_EXCEEDED),
        (ValueError("malformed sanitized row"), grpc.StatusCode.DATA_LOSS),
    ],
)
def test_database_failures_map_to_exact_sanitized_status(
    error: Exception,
    expected: grpc.StatusCode,
) -> None:
    settings = _settings()
    server = create_disclosure_server(settings, _FailingRepository(error))
    server.start()
    channel = grpc.insecure_channel(settings.bind_address)
    try:
        with pytest.raises(grpc.RpcError) as raised:
            _get_disclosure_events(
                disclosure_observation_pb2_grpc.DisclosureObservationServiceStub(channel),
                _request(),
                timeout=0.5,
            )
        assert raised.value.code() == expected
        assert "sanitized" not in raised.value.details()
    finally:
        channel.close()
        server.stop(grace=0).wait(timeout=2)


class _CancellableConnection:
    def __init__(self) -> None:
        self.cancelled = threading.Event()

    def cancel(self) -> None:
        self.cancelled.set()


class _BlockingRepository:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.connection = _CancellableConnection()

    def load(
        self,
        *,
        symbol: str,
        corp_code: str | None,
        window_from: date,
        window_to: date,
        cancellation: QueryCancellation,
    ) -> StoredDisclosureBatch:
        cancellation.attach(self.connection)
        self.started.set()
        self.connection.cancelled.wait(timeout=2)
        cancellation.detach(self.connection)
        raise TimeoutError


def test_client_cancellation_cancels_active_database_resource() -> None:
    repository = _BlockingRepository()
    settings = _settings()
    server = create_disclosure_server(settings, repository)
    server.start()
    channel = grpc.insecure_channel(settings.bind_address)
    try:
        with pytest.raises(grpc.RpcError):
            _get_disclosure_events(
                disclosure_observation_pb2_grpc.DisclosureObservationServiceStub(channel),
                _request(),
                timeout=0.1,
            )
        assert repository.started.wait(timeout=1)
        assert repository.connection.cancelled.wait(timeout=1)
    finally:
        channel.close()
        server.stop(grace=0).wait(timeout=2)


def test_cancellation_before_connection_acquisition_fails_before_query_work() -> None:
    cancellation = QueryCancellation()
    cancellation.cancel()

    with pytest.raises(TimeoutError):
        cancellation.raise_if_cancelled()


class _ConcurrencyRepository:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.calls = 0

    def load(
        self,
        *,
        symbol: str,
        corp_code: str | None,
        window_from: date,
        window_to: date,
        cancellation: QueryCancellation,
    ) -> StoredDisclosureBatch:
        with self.lock:
            self.active += 1
            self.calls += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.release.wait(timeout=2)
            return _batch()
        finally:
            with self.lock:
                self.active -= 1


def test_ninth_concurrent_rpc_is_bounded_before_a_ninth_repository_call() -> None:
    repository = _ConcurrencyRepository()
    settings = _settings()
    server = create_disclosure_server(settings, repository)
    server.start()
    channel = grpc.insecure_channel(settings.bind_address)
    executor = futures.ThreadPoolExecutor(max_workers=9)
    try:
        stub = disclosure_observation_pb2_grpc.DisclosureObservationServiceStub(channel)
        calls = [
            executor.submit(_get_disclosure_events, stub, _request(), timeout=1.5)
            for _ in range(9)
        ]
        deadline = time.monotonic() + 1
        while repository.calls < 8 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert repository.calls == 8
        error_deadline = time.monotonic() + 0.5
        ninth_errors: list[BaseException] = []
        while not ninth_errors and time.monotonic() < error_deadline:
            ninth_errors = [
                error
                for call in calls
                if call.done() and (error := call.exception()) is not None
            ]
            time.sleep(0.01)
        assert any(
            isinstance(error, grpc.RpcError)
            and error.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
            for error in ninth_errors
        )
        repository.release.set()
        for call in calls:
            try:
                call.result(timeout=2)
            except grpc.RpcError as error:
                assert error.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
        assert repository.max_active == 8
        assert repository.calls == 8
    finally:
        repository.release.set()
        executor.shutdown(wait=True)
        channel.close()
        server.stop(grace=0).wait(timeout=2)


def test_duplicate_event_or_source_reference_is_not_silently_truncated() -> None:
    event = _batch().events[0]
    duplicate_batch = StoredDisclosureBatch(
        symbol="005930",
        corp_code="00126380",
        observed_at=datetime(2026, 7, 24, 1, 2, 3, tzinfo=UTC),
        mapping_version="s1.2-v1",
        complete=True,
        events=(event, event),
    )
    repository = FakeDisclosureRepository(duplicate_batch)
    settings = _settings()
    server = create_disclosure_server(settings, repository)
    server.start()
    channel = grpc.insecure_channel(settings.bind_address)
    try:
        with pytest.raises(grpc.RpcError) as error:
            _get_disclosure_events(
                disclosure_observation_pb2_grpc.DisclosureObservationServiceStub(channel),
                _request(),
                timeout=0.5,
            )
        assert error.value.code() == grpc.StatusCode.DATA_LOSS
    finally:
        channel.close()
        server.stop(grace=0).wait(timeout=2)


def test_duplicate_source_reference_across_distinct_events_fails_closed() -> None:
    first, second = _batch(event_count=2).events
    duplicate_source_batch = StoredDisclosureBatch(
        symbol="005930",
        corp_code="00126380",
        observed_at=datetime(2026, 7, 24, 1, 2, 3, tzinfo=UTC),
        mapping_version="s1.2-v1",
        complete=True,
        events=(
            first,
            StoredDisclosureEvent(
                symbol=second.symbol,
                corp_code=second.corp_code,
                event_code=second.event_code,
                receipt_no=second.receipt_no,
                occurred_on=second.occurred_on,
                observed_at=second.observed_at,
                mapping_version=second.mapping_version,
                source_refs=first.source_refs,
                attributes=second.attributes,
            ),
        ),
    )
    repository = FakeDisclosureRepository(duplicate_source_batch)
    settings = _settings()
    server = create_disclosure_server(settings, repository)
    server.start()
    channel = grpc.insecure_channel(settings.bind_address)
    try:
        with pytest.raises(grpc.RpcError) as error:
            _get_disclosure_events(
                disclosure_observation_pb2_grpc.DisclosureObservationServiceStub(channel),
                _request(),
                timeout=0.5,
            )
        assert error.value.code() == grpc.StatusCode.DATA_LOSS
    finally:
        channel.close()
        server.stop(grace=0).wait(timeout=2)


def test_source_reference_overflow_is_typed_incomplete_instead_of_partial_success() -> None:
    events = []
    for index in range(51):
        event = _batch(event_count=51).events[index]
        events.append(
            StoredDisclosureEvent(
                symbol=event.symbol,
                corp_code=event.corp_code,
                event_code=event.event_code,
                receipt_no=event.receipt_no,
                occurred_on=event.occurred_on,
                observed_at=event.observed_at,
                mapping_version=event.mapping_version,
                source_refs=(
                    f"{index * 2 + 1:064x}",
                    f"{index * 2 + 2:064x}",
                ),
                attributes=event.attributes,
            )
        )
    repository = FakeDisclosureRepository(
        StoredDisclosureBatch(
            symbol="005930",
            corp_code="00126380",
            observed_at=datetime(2026, 7, 24, 1, 2, 3, tzinfo=UTC),
            mapping_version="s1.2-v1",
            complete=True,
            events=tuple(events),
        )
    )
    settings = _settings()
    server = create_disclosure_server(settings, repository)
    server.start()
    channel = grpc.insecure_channel(settings.bind_address)
    try:
        with pytest.raises(grpc.RpcError) as error:
            _get_disclosure_events(
                disclosure_observation_pb2_grpc.DisclosureObservationServiceStub(channel),
                _request(),
                timeout=0.5,
            )
        assert error.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    finally:
        channel.close()
        server.stop(grace=0).wait(timeout=2)


def test_rpc_source_does_not_import_provider_http_or_report_name_matching() -> None:
    from app import disclosure_rpc

    source = disclosure_rpc.__loader__.get_source(disclosure_rpc.__name__)  # type: ignore[union-attr]
    assert source is not None
    assert "httpx" not in source
    assert "OpenDARTClient" not in source
    assert "report_nm" not in source
