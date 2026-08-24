from __future__ import annotations

from dataclasses import replace
from threading import Thread

import pytest

from app.async_worker.poison_recorder import (
    HttpPoisonRecorderClient,
    PoisonReceipt,
    PoisonReceiptError,
    PoisonRecorderHttpServer,
)


SECRET = "poison-recorder-shared-secret-0000000001"


class FakeRecorder:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.receipts: list[PoisonReceipt] = []

    def record(self, receipt: PoisonReceipt) -> bool:
        receipt.validate()
        self.receipts.append(receipt)
        return self.result


def _receipt() -> PoisonReceipt:
    return PoisonReceipt(
        event_id="evt_poison_00000000000000000000000000000001",
        event_type="artifact.ingest-requested.v1",
        payload_hash="sha256:" + "a" * 64,
        source_topic="artifact.ingest-requested.v1",
        source_partition=1,
        source_offset=7,
        attempt=1,
        failure_code="INVALID_EVENT_PAYLOAD",
        partition_key="hmac-sha256:" + "c" * 64,
    )


def _running_server(recorder: FakeRecorder) -> tuple[PoisonRecorderHttpServer, Thread]:
    server = PoisonRecorderHttpServer(("127.0.0.1", 0), recorder, SECRET)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_client_receives_durable_ack_only_after_recorder_success() -> None:
    recorder = FakeRecorder()
    server, thread = _running_server(recorder)
    try:
        client = HttpPoisonRecorderClient(
            f"http://127.0.0.1:{server.server_port}/internal/kafka/poison-receipts", SECRET
        )
        assert client.record(_receipt())
        assert recorder.receipts == [_receipt()]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_recorder_failure_never_returns_durable_ack() -> None:
    server, thread = _running_server(FakeRecorder(result=False))
    try:
        client = HttpPoisonRecorderClient(
            f"http://127.0.0.1:{server.server_port}/internal/kafka/poison-receipts", SECRET
        )
        with pytest.raises(PoisonReceiptError):
            client.record(_receipt())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_wrong_secret_is_rejected_without_recording() -> None:
    recorder = FakeRecorder()
    server, thread = _running_server(recorder)
    try:
        client = HttpPoisonRecorderClient(
            f"http://127.0.0.1:{server.server_port}/internal/kafka/poison-receipts",
            "wrong-secret-that-is-still-long-enough-000001",
        )
        with pytest.raises(PoisonReceiptError):
            client.record(_receipt())
        assert recorder.receipts == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_non_loopback_client_and_invalid_receipt_fail_closed() -> None:
    with pytest.raises(ValueError):
        HttpPoisonRecorderClient("http://localhost:18082/internal/kafka/poison-receipts", SECRET)
    invalid = replace(_receipt(), source_partition=3)
    with pytest.raises(PoisonReceiptError):
        invalid.validate()


def test_server_binds_numeric_loopback_only() -> None:
    with pytest.raises(ValueError):
        PoisonRecorderHttpServer(("0.0.0.0", 18082), FakeRecorder(), SECRET)
