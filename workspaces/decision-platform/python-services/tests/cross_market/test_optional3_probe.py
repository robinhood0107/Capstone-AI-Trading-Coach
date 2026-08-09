from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.cross_market.optional3_probe import (
    Optional3ProbeError,
    Optional3ProbeExecutionBinding,
    Optional3ProbeExecutor,
    Optional3ProbeHttpResponse,
    Optional3ProbePacket,
    StdlibOptional3ProbeTransport,
    optional3_endpoint_set_digest,
    optional3_request_plan_digest,
)


_NOW = datetime(2026, 8, 9, 4, 5, 6, tzinfo=UTC)


def _packet(
    *,
    operation: str = "FINNHUB_RECOMMENDATION",
    provider_family: str = "FINNHUB_OPTIONAL3",
    symbol: str = "AAPL",
    date: str = "NONE",
    head_sha: str = "a" * 40,
    tree_sha256: str = "b" * 64,
    ci_digest: str = "c" * 64,
    security_digest: str = "d" * 64,
) -> Optional3ProbePacket:
    return Optional3ProbePacket(
        approval_id="o3p_" + "a" * 32,
        ci_digest=ci_digest,
        cost_cap_microusd=10_000,
        date=date,
        endpoint_set_digest=optional3_endpoint_set_digest(),
        expires_at=_NOW + timedelta(minutes=15),
        head_sha=head_sha,
        logical_call_cap=1,
        nonce="nonce-" + "b" * 24,
        operation=operation,
        operator="local-owner",
        physical_call_cap=1,
        provider_family=provider_family,
        request_plan_digest=optional3_request_plan_digest(
            operation=operation,
            symbol=symbol,
            date=date,
        ),
        retry_count=0,
        security_digest=security_digest,
        symbol=symbol,
        tracked_raw_artifact_count=0,
        tree_sha256=tree_sha256,
    )


def _binding(
    *,
    head_sha: str = "a" * 40,
    tree_sha256: str = "b" * 64,
    ci_digest: str = "c" * 64,
    security_digest: str = "d" * 64,
) -> Optional3ProbeExecutionBinding:
    return Optional3ProbeExecutionBinding(
        ci_digest=ci_digest,
        head_sha=head_sha,
        security_digest=security_digest,
        tree_sha256=tree_sha256,
    )


class _RecordingTransport:
    def __init__(self, *, response: Optional3ProbeHttpResponse) -> None:
        self.calls: list[dict[str, object]] = []
        self._response = response

    def get(
        self,
        *,
        hostname: str,
        target: str,
        api_key: str,
        expires_at: datetime,
        timeout_seconds: float,
        maximum_response_bytes: int,
    ) -> Optional3ProbeHttpResponse:
        self.calls.append(
            {
                "apiKey": api_key,
                "hostname": hostname,
                "maximumResponseBytes": maximum_response_bytes,
                "target": target,
                "timeoutSeconds": timeout_seconds,
            }
        )
        return self._response


class _StaticResolver:
    def resolve(self, hostname: str, *, timeout_seconds: float) -> list[str]:
        assert hostname == "finnhub.io"
        assert timeout_seconds > 0
        return ["8.8.8.8"]


class _Response:
    status_code = 200
    headers = {"content-type": "application/json", "content-length": "15"}

    def set_read_timeout_seconds(self, *, timeout_seconds: float) -> None:
        assert timeout_seconds > 0

    def iter_raw(self, *, chunk_size: int):  # type: ignore[no-untyped-def]
        assert chunk_size == 16 * 1024
        yield b'[]'


class _Connection:
    peer_ip = "8.8.8.8"

    def __init__(self) -> None:
        self.captured: dict[str, object] = {}

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(
        self,
        *,
        target: str,
        headers: dict[str, str],
        read_timeout_seconds: float,
    ) -> _Response:
        self.captured = {
            "headers": headers,
            "target": target,
            "timeoutSeconds": read_timeout_seconds,
        }
        return _Response()


class _PinnedTransport:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def connect(self, **_: object) -> _Connection:
        return self.connection


def _private_root(tmp_path: Path) -> Path:
    root = tmp_path / "optional3-control"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return root


def test_packet_binds_fixed_provider_operation_and_nonsecret_request_plan() -> None:
    packet = _packet()

    assert packet.provider_family == "FINNHUB_OPTIONAL3"
    assert packet.operation == "FINNHUB_RECOMMENDATION"
    assert packet.request_plan_digest == optional3_request_plan_digest(
        operation="FINNHUB_RECOMMENDATION",
        symbol="AAPL",
        date="NONE",
    )

    with pytest.raises(Optional3ProbeError, match="OPTIONAL3_PROBE_OPERATION_PROVIDER_INVALID"):
        _packet(operation="TWELVE_DATA_TIME_SERIES", provider_family="FINNHUB_OPTIONAL3")

    with pytest.raises(Optional3ProbeError, match="OPTIONAL3_PROBE_REQUEST_PLAN_DIGEST_INVALID"):
        Optional3ProbePacket.from_local_document(
            {
                **packet.to_local_document(),
                "requestPlanDigest": "f" * 64,
            }
        )


def test_executor_rejects_drift_before_transport_or_claim_write(tmp_path: Path) -> None:
    control_root = _private_root(tmp_path)
    transport = _RecordingTransport(
        response=Optional3ProbeHttpResponse(status_code=200, body=b'[]'),
    )
    executor = Optional3ProbeExecutor(
        control_root=control_root,
        transport=transport,
    )

    with pytest.raises(Optional3ProbeError, match="OPTIONAL3_PROBE_EXECUTION_BINDING_DRIFT"):
        executor.execute(
            packet=_packet(),
            binding=_binding(head_sha="f" * 40),
            api_key="test-secret-value",
            now=_NOW,
        )

    assert transport.calls == []
    assert list(control_root.iterdir()) == []


def test_executor_uses_one_fixed_finnhub_request_and_keeps_key_body_and_query_out_of_receipt(
    tmp_path: Path,
) -> None:
    control_root = _private_root(tmp_path)
    api_key = "test-secret-value"
    raw_body = b'[{"symbol":"AAPL","buy":1,"hold":2,"sell":3}]'
    transport = _RecordingTransport(
        response=Optional3ProbeHttpResponse(status_code=200, body=raw_body),
    )
    executor = Optional3ProbeExecutor(
        control_root=control_root,
        transport=transport,
    )

    receipt = executor.execute(
        packet=_packet(),
        binding=_binding(),
        api_key=api_key,
        now=_NOW,
    )

    assert transport.calls == [
        {
            "apiKey": api_key,
            "hostname": "finnhub.io",
            "maximumResponseBytes": 262_144,
            "target": "/api/v1/stock/recommendation?symbol=AAPL",
            "timeoutSeconds": 10.0,
        }
    ]
    assert receipt.outcome == "SUCCESS"
    assert receipt.logical_call_count == 1
    assert receipt.physical_call_count == 1
    assert receipt.provider_status_class == "HTTP_2XX"
    assert receipt.projection_hash is not None

    receipt_document = receipt.to_local_document()
    rendered = json.dumps(receipt_document, sort_keys=True)
    assert api_key not in rendered
    assert raw_body.decode("utf-8") not in rendered
    assert "?symbol=AAPL" not in rendered
    assert receipt_document["rawProviderDataStored"] is False
    assert receipt_document["rawHeaderStored"] is False
    assert receipt_document["rawQueryStored"] is False

    claim_paths = sorted(control_root.glob("consumed-*.json"))
    receipt_paths = sorted(control_root.glob("receipt-*.json"))
    assert len(claim_paths) == 1
    assert len(receipt_paths) == 1
    assert os.stat(claim_paths[0]).st_mode & 0o777 == 0o600
    assert os.stat(receipt_paths[0]).st_mode & 0o777 == 0o600
    assert api_key not in receipt_paths[0].read_text(encoding="utf-8")
    assert raw_body.decode("utf-8") not in receipt_paths[0].read_text(encoding="utf-8")


def test_executor_accepts_the_fixed_finnhub_earnings_array_shape(tmp_path: Path) -> None:
    control_root = _private_root(tmp_path)
    transport = _RecordingTransport(
        response=Optional3ProbeHttpResponse(
            status_code=200,
            body=b'[{"actual":1.23,"estimate":1.2,"period":"2026-06-30"}]',
        ),
    )

    receipt = Optional3ProbeExecutor(
        control_root=control_root,
        transport=transport,
    ).execute(
        packet=_packet(operation="FINNHUB_EARNINGS"),
        binding=_binding(),
        api_key="test-secret-value",
        now=_NOW,
    )

    assert receipt.outcome == "SUCCESS"
    assert transport.calls[0]["target"] == "/api/v1/stock/earnings?limit=1&symbol=AAPL"


def test_stdlib_transport_pins_dns_and_adds_secret_only_at_request_handoff() -> None:
    connection = _Connection()
    transport = StdlibOptional3ProbeTransport()
    transport._resolver = _StaticResolver()  # type: ignore[assignment]
    transport._transport = _PinnedTransport(connection)  # type: ignore[assignment]

    response = transport.get(
        hostname="finnhub.io",
        target="/api/v1/stock/recommendation?symbol=AAPL",
        api_key="test-secret-value",
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
        timeout_seconds=10.0,
        maximum_response_bytes=262_144,
    )

    assert response == Optional3ProbeHttpResponse(status_code=200, body=b'[]')
    assert connection.captured["target"] == "/api/v1/stock/recommendation?symbol=AAPL&token=test-secret-value"
    assert connection.captured["headers"] == {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Connection": "close",
        "Host": "finnhub.io",
        "User-Agent": "capstone-s48-optional3-probe/1",
    }


def test_first_provider_failure_is_receipted_and_packet_cannot_be_reused(tmp_path: Path) -> None:
    control_root = _private_root(tmp_path)
    transport = _RecordingTransport(
        response=Optional3ProbeHttpResponse(status_code=429, body=b'{"error":"quota"}'),
    )
    executor = Optional3ProbeExecutor(
        control_root=control_root,
        transport=transport,
    )
    packet = _packet()

    receipt = executor.execute(
        packet=packet,
        binding=_binding(),
        api_key="test-secret-value",
        now=_NOW,
    )

    assert receipt.outcome == "FAILED"
    assert receipt.logical_call_count == 1
    assert receipt.physical_call_count == 1
    assert receipt.provider_status_class == "HTTP_4XX"
    assert receipt.projection_hash is None
    assert len(transport.calls) == 1

    with pytest.raises(Optional3ProbeError, match="OPTIONAL3_PROBE_PACKET_ALREADY_CONSUMED"):
        executor.execute(
            packet=packet,
            binding=_binding(),
            api_key="test-secret-value",
            now=_NOW,
        )
    assert len(transport.calls) == 1


def test_post_request_receipt_failure_reports_the_one_physical_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = Optional3ProbeExecutor(
        control_root=_private_root(tmp_path),
        transport=_RecordingTransport(
            response=Optional3ProbeHttpResponse(status_code=200, body=b'[]'),
        ),
    )

    def reject_receipt(**_: object) -> None:
        raise Optional3ProbeError("OPTIONAL3_PROBE_RECEIPT_UNAVAILABLE", physical_call_count=1)

    monkeypatch.setattr(executor, "_write_receipt", reject_receipt)
    with pytest.raises(Optional3ProbeError, match="OPTIONAL3_PROBE_RECEIPT_UNAVAILABLE") as caught:
        executor.execute(
            packet=_packet(),
            binding=_binding(),
            api_key="test-secret-value",
            now=_NOW,
        )

    assert caught.value.physical_call_count == 1


def test_packet_file_reader_rejects_noncanonical_or_unsafe_local_control_files(tmp_path: Path) -> None:
    control_root = _private_root(tmp_path)
    packet = _packet()
    packet_path = control_root / "approval.json"
    packet_path.write_text(json.dumps(packet.to_local_document()) + "\n", encoding="utf-8")
    packet_path.chmod(0o600)

    with pytest.raises(Optional3ProbeError, match="OPTIONAL3_PROBE_PACKET_CANONICAL_INVALID"):
        Optional3ProbePacket.load_from_control_root(
            control_root=control_root,
            relative_path="approval.json",
            now=_NOW,
        )

    canonical = json.dumps(
        packet.to_local_document(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    packet_path.write_text(canonical, encoding="utf-8")
    packet_path.chmod(0o644)
    with pytest.raises(Optional3ProbeError, match="OPTIONAL3_PROBE_PACKET_UNSAFE"):
        Optional3ProbePacket.load_from_control_root(
            control_root=control_root,
            relative_path="approval.json",
            now=_NOW,
        )
