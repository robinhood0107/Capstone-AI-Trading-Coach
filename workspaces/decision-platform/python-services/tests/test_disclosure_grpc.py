from __future__ import annotations

import socket
from datetime import UTC, date, datetime

import grpc
import pytest
from grpc_reflection.v1alpha import reflection_pb2, reflection_pb2_grpc

from app.disclosure_rpc import (
    StoredDisclosureBatch,
    StoredDisclosureEvent,
    create_disclosure_server,
)
from app.generated import (
    disclosure_observation_pb2,
    disclosure_observation_pb2_grpc,
)
from app.grpc_server import GrpcServerSettings


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
            source_ref=f"{index + 1:064x}",
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
        corp_code="00126380",
        as_of="2026-07-24",
        window_from="2026-06-24",
        window_to="2026-07-24",
    )


def test_real_business_rpc_roundtrip_is_loopback_single_call_without_reflection() -> None:
    repository = FakeDisclosureRepository(_batch())
    settings = GrpcServerSettings(bind_address=_loopback_address())
    server = create_disclosure_server(settings, repository)
    server.start()
    channel = grpc.insecure_channel(settings.bind_address)
    try:
        response = disclosure_observation_pb2_grpc.DisclosureObservationServiceStub(
            channel
        ).GetDisclosureEvents(_request(), timeout=0.5)

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
    settings = GrpcServerSettings(bind_address=_loopback_address())
    server = create_disclosure_server(settings, repository)
    server.start()
    channel = grpc.insecure_channel(settings.bind_address)
    try:
        stub = disclosure_observation_pb2_grpc.DisclosureObservationServiceStub(channel)
        with pytest.raises(grpc.RpcError) as response_error:
            stub.GetDisclosureEvents(_request(), timeout=0.5)
        assert response_error.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
        assert repository.calls == 1

        oversized = _request()
        oversized.symbol = "9" * (256 * 1024)
        with pytest.raises(grpc.RpcError) as request_error:
            stub.GetDisclosureEvents(oversized, timeout=0.5)
        assert request_error.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
        assert repository.calls == 1
    finally:
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
    settings = GrpcServerSettings(bind_address=_loopback_address())
    server = create_disclosure_server(settings, repository)
    server.start()
    channel = grpc.insecure_channel(settings.bind_address)
    try:
        with pytest.raises(grpc.RpcError) as error:
            disclosure_observation_pb2_grpc.DisclosureObservationServiceStub(
                channel
            ).GetDisclosureEvents(_request(), timeout=0.5)
        assert error.value.code() == grpc.StatusCode.DATA_LOSS
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
