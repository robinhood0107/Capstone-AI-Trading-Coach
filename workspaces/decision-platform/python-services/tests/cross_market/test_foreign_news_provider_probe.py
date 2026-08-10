from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import app.cross_market.foreign_news_provider_probe as provider_probe_module

from app.cross_market.foreign_news_provider_probe import (
    ForeignNewsProviderProbeError,
    ForeignNewsProviderProbeExecutionBinding,
    ForeignNewsProviderProbeExecutor,
    ForeignNewsProviderProbeHttpResponse,
    ForeignNewsProviderProbePacket,
    StdlibForeignNewsProviderProbeTransport,
    foreign_news_provider_endpoint_set_digest,
    foreign_news_provider_request_plan_digest,
)


_NOW = datetime(2026, 8, 10, 2, 3, 4, tzinfo=UTC)


def _packet(
    *,
    operation: str = "FINNHUB_COMPANY_NEWS",
    provider_family: str = "FINNHUB_PERSONAL_LOCAL",
    symbol: str = "AAPL",
    date: str = "2026-08-07",
    head_sha: str = "a" * 40,
    tree_sha256: str = "b" * 64,
    ci_digest: str = "c" * 64,
    security_digest: str = "d" * 64,
) -> ForeignNewsProviderProbePacket:
    return ForeignNewsProviderProbePacket(
        approval_id="fnp_" + "a" * 32,
        ci_digest=ci_digest,
        cost_cap_microusd=10_000,
        date=date,
        endpoint_set_digest=foreign_news_provider_endpoint_set_digest(),
        expires_at=_NOW + timedelta(minutes=15),
        head_sha=head_sha,
        logical_call_cap=1,
        nonce="nonce-" + "b" * 24,
        operation=operation,
        operator="local-owner",
        physical_call_cap=1,
        provider_family=provider_family,
        request_plan_digest=foreign_news_provider_request_plan_digest(
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
) -> ForeignNewsProviderProbeExecutionBinding:
    return ForeignNewsProviderProbeExecutionBinding(
        ci_digest=ci_digest,
        head_sha=head_sha,
        security_digest=security_digest,
        tree_sha256=tree_sha256,
    )


class _RecordingTransport:
    def __init__(self, *, response: ForeignNewsProviderProbeHttpResponse) -> None:
        self.calls: list[dict[str, object]] = []
        self._response = response

    def get(
        self,
        *,
        operation: str,
        hostname: str,
        target: str,
        api_key: str | None,
        user_agent: str,
        expires_at: datetime,
        timeout_seconds: float,
        maximum_response_bytes: int,
    ) -> ForeignNewsProviderProbeHttpResponse:
        self.calls.append(
            {
                "apiKey": api_key,
                "hostname": hostname,
                "maximumResponseBytes": maximum_response_bytes,
                "operation": operation,
                "target": target,
                "timeoutSeconds": timeout_seconds,
                "userAgent": user_agent,
            }
        )
        return self._response


class _RecordingAnalyzer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def analyze(self, *, lane_id: str, texts: tuple[str, ...]) -> None:
        self.calls.append((lane_id, texts))


class _StaticResolver:
    def resolve(self, hostname: str, *, timeout_seconds: float) -> list[str]:
        assert hostname in {"finnhub.io", "www.sec.gov"}
        assert timeout_seconds > 0
        return ["8.8.8.8"]


class _Response:
    def __init__(self, *, body: bytes, content_type: str) -> None:
        self.status_code = 200
        self.headers = {"content-type": content_type, "content-length": str(len(body))}
        self._body = body

    def set_read_timeout_seconds(self, *, timeout_seconds: float) -> None:
        assert timeout_seconds > 0

    def iter_raw(self, *, chunk_size: int):  # type: ignore[no-untyped-def]
        assert chunk_size == 16 * 1024
        yield self._body


class _Connection:
    peer_ip = "8.8.8.8"

    def __init__(self, *, body: bytes, content_type: str) -> None:
        self._response = _Response(body=body, content_type=content_type)
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
        return self._response


class _PinnedTransport:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def connect(self, **_: object) -> _Connection:
        return self.connection


def _private_root(tmp_path: Path) -> Path:
    root = tmp_path / "foreign-news-control"
    root.mkdir(mode=0o700, parents=True)
    root.chmod(0o700)
    return root


def test_packet_binds_fixed_provider_operation_and_nonsecret_request_plan() -> None:
    packet = _packet()

    assert packet.provider_family == "FINNHUB_PERSONAL_LOCAL"
    assert packet.operation == "FINNHUB_COMPANY_NEWS"
    assert packet.request_plan_digest == foreign_news_provider_request_plan_digest(
        operation="FINNHUB_COMPANY_NEWS",
        symbol="AAPL",
        date="2026-08-07",
    )

    with pytest.raises(ForeignNewsProviderProbeError, match="FOREIGN_NEWS_PROBE_OPERATION_PROVIDER_INVALID"):
        _packet(
            operation="FED_OFFICIAL_RELEASES",
            provider_family="FINNHUB_PERSONAL_LOCAL",
            date="NONE",
        )

    with pytest.raises(ForeignNewsProviderProbeError, match="FOREIGN_NEWS_PROBE_REQUEST_PLAN_DIGEST_INVALID"):
        ForeignNewsProviderProbePacket.from_local_document(
            {
                **packet.to_local_document(),
                "requestPlanDigest": "f" * 64,
            }
        )


def test_packet_accepts_the_same_korean_symbol_alphabet_as_foreign_news_response() -> None:
    packet = _packet(symbol="005930.KS")

    assert packet.symbol == "005930.KS"
    assert packet.request_plan_digest == foreign_news_provider_request_plan_digest(
        operation="FINNHUB_COMPANY_NEWS",
        symbol="005930.KS",
        date="2026-08-07",
    )


def test_executor_rejects_evidence_drift_before_transport_or_packet_claim(tmp_path: Path) -> None:
    control_root = _private_root(tmp_path)
    transport = _RecordingTransport(
        response=ForeignNewsProviderProbeHttpResponse(
            status_code=200,
            body=b'[{"headline":"profit improved","summary":"guidance raised"}]',
            content_type="application/json",
        ),
    )
    executor = ForeignNewsProviderProbeExecutor(control_root=control_root, transport=transport)

    with pytest.raises(ForeignNewsProviderProbeError, match="FOREIGN_NEWS_PROBE_EXECUTION_BINDING_DRIFT"):
        executor.execute(
            packet=_packet(),
            binding=_binding(head_sha="f" * 40),
            api_key="test-secret-value",
            analyzer=_RecordingAnalyzer(),
            now=_NOW,
            user_agent="test-contact@example.invalid",
        )

    assert transport.calls == []
    assert list(control_root.iterdir()) == []


def test_finnhub_company_news_is_transiently_analyzed_and_receipt_retains_no_raw_data(
    tmp_path: Path,
) -> None:
    control_root = _private_root(tmp_path)
    api_key = "test-secret-value"
    raw_body = b'[{"headline":"profit improved","summary":"guidance raised","url":"https://example.invalid/raw"}]'
    transport = _RecordingTransport(
        response=ForeignNewsProviderProbeHttpResponse(
            status_code=200,
            body=raw_body,
            content_type="application/json",
        ),
    )
    analyzer = _RecordingAnalyzer()
    result = ForeignNewsProviderProbeExecutor(control_root=control_root, transport=transport).execute(
        packet=_packet(),
        binding=_binding(),
        api_key=api_key,
        analyzer=analyzer,
        now=_NOW,
        user_agent="test-contact@example.invalid",
    )

    assert result.receipt.outcome == "SUCCESS"
    assert result.receipt.provider_status_class == "HTTP_2XX"
    assert result.receipt.physical_call_count == 1
    assert result.aggregate.lane_id == "FINNHUB_PERSONAL_LOCAL"
    assert result.aggregate.state == "AVAILABLE"
    assert result.aggregate.content_hash is None
    assert result.aggregate.official_release_locator is None
    assert analyzer.calls == [
        ("FINNHUB_PERSONAL_LOCAL", ("profit improved\nguidance raised",)),
    ]
    assert transport.calls == [
        {
            "apiKey": api_key,
            "hostname": "finnhub.io",
            "maximumResponseBytes": 262_144,
            "operation": "FINNHUB_COMPANY_NEWS",
            "target": "/api/v1/company-news?from=2026-08-07&symbol=AAPL&to=2026-08-07",
            "timeoutSeconds": 10.0,
            "userAgent": "test-contact@example.invalid",
        }
    ]
    receipt = result.receipt.to_local_document()
    serialized = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    assert api_key not in serialized
    assert "profit improved" not in serialized
    assert "example.invalid" not in serialized
    assert receipt["rawProviderDataStored"] is False
    assert receipt["articleMetadataStored"] is False
    assert receipt["rawQueryStored"] is False
    assert receipt["rawHeaderStored"] is False


def test_sec_and_fed_official_html_are_bounded_transient_parse_with_hash_only_provenance(
    tmp_path: Path,
) -> None:
    for operation, provider_family, expected_lane, expected_locator in (
        ("SEC_OFFICIAL_RELEASES", "SEC_OFFICIAL", "SEC_OFFICIAL", "SEC_OFFICIAL_RELEASES"),
        ("FED_OFFICIAL_RELEASES", "FED_OFFICIAL", "FED_OFFICIAL", "FED_OFFICIAL_RELEASES"),
    ):
        control_root = _private_root(tmp_path / operation)
        transport = _RecordingTransport(
            response=ForeignNewsProviderProbeHttpResponse(
                status_code=200,
                body=b"<html><body>Official release says outlook improved.</body></html>",
                content_type="text/html",
            ),
        )
        analyzer = _RecordingAnalyzer()
        result = ForeignNewsProviderProbeExecutor(control_root=control_root, transport=transport).execute(
            packet=_packet(
                operation=operation,
                provider_family=provider_family,
                date="NONE",
            ),
            binding=_binding(),
            api_key=None,
            analyzer=analyzer,
            now=_NOW,
            user_agent="test-contact@example.invalid",
        )

        assert result.receipt.outcome == "SUCCESS"
        assert result.aggregate.lane_id == expected_lane
        assert result.aggregate.state == "AVAILABLE"
        assert result.aggregate.content_hash is not None
        assert result.aggregate.official_release_locator == expected_locator
        assert analyzer.calls == [(expected_lane, ("Official release says outlook improved.",))]
        assert transport.calls[0]["apiKey"] is None


def test_packet_is_consumed_after_first_failed_provider_call_and_never_reused(tmp_path: Path) -> None:
    control_root = _private_root(tmp_path)
    transport = _RecordingTransport(
        response=ForeignNewsProviderProbeHttpResponse(
            status_code=429,
            body=b'{"error":"quota"}',
            content_type="application/json",
        ),
    )
    executor = ForeignNewsProviderProbeExecutor(control_root=control_root, transport=transport)
    packet = _packet()

    result = executor.execute(
        packet=packet,
        binding=_binding(),
        api_key="test-secret-value",
        analyzer=_RecordingAnalyzer(),
        now=_NOW,
        user_agent="test-contact@example.invalid",
    )

    assert result.receipt.outcome == "FAILED"
    assert result.receipt.provider_status_class == "HTTP_4XX"
    assert result.receipt.physical_call_count == 1
    assert result.aggregate.state == "ABSTAIN"
    assert len(transport.calls) == 1
    with pytest.raises(ForeignNewsProviderProbeError, match="FOREIGN_NEWS_PROBE_PACKET_ALREADY_CONSUMED"):
        executor.execute(
            packet=packet,
            binding=_binding(),
            api_key="test-secret-value",
            analyzer=_RecordingAnalyzer(),
            now=_NOW,
            user_agent="test-contact@example.invalid",
        )
    assert len(transport.calls) == 1


def test_pre_handoff_transport_failure_seals_an_exact_zero_call_receipt(tmp_path: Path) -> None:
    """DNS/connection preflight failure는 socket을 열지 않았으므로 receipt도 0으로 남아야 한다."""

    class _PreHandoffFailureTransport:
        calls = 0

        def get(self, **_: object) -> ForeignNewsProviderProbeHttpResponse:
            self.calls += 1
            raise ForeignNewsProviderProbeError(
                "FOREIGN_NEWS_PROBE_TRANSPORT_UNAVAILABLE",
                physical_call_count=0,
            )

    control_root = _private_root(tmp_path)
    transport = _PreHandoffFailureTransport()
    packet = _packet()
    result = ForeignNewsProviderProbeExecutor(
        control_root=control_root,
        transport=transport,
        now_provider=lambda: _NOW + timedelta(seconds=2),
    ).execute(
        packet=packet,
        binding=_binding(),
        api_key="test-secret-value",
        analyzer=_RecordingAnalyzer(),
        now=_NOW,
        user_agent="test-contact@example.invalid",
    )

    assert transport.calls == 1
    assert result.receipt.outcome == "NOT_EXECUTED"
    assert result.receipt.lane_state == "ABSTAIN"
    assert result.receipt.logical_call_count == 0
    assert result.receipt.physical_call_count == 0
    assert result.receipt.provider_status_class == "NOT_ATTEMPTED"
    assert result.receipt.completed_at == _NOW + timedelta(seconds=2)
    stored = result.receipt.to_local_document()
    assert stored["physicalCallCount"] == 0
    assert stored["providerStatusClass"] == "NOT_ATTEMPTED"
    with pytest.raises(ForeignNewsProviderProbeError, match="FOREIGN_NEWS_PROBE_PACKET_ALREADY_CONSUMED"):
        ForeignNewsProviderProbeExecutor(control_root=control_root, transport=transport).execute(
            packet=packet,
            binding=_binding(),
            api_key="test-secret-value",
            analyzer=_RecordingAnalyzer(),
            now=_NOW,
            user_agent="test-contact@example.invalid",
        )


def test_pre_handoff_failure_receipt_write_error_preserves_zero_physical_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """provider handoff 전 실패는 local receipt write 실패와 무관하게 physical call 0이다."""

    class _PreHandoffFailureTransport:
        def get(self, **_: object) -> ForeignNewsProviderProbeHttpResponse:
            raise ForeignNewsProviderProbeError(
                "FOREIGN_NEWS_PROBE_TRANSPORT_UNAVAILABLE",
                physical_call_count=0,
            )

    original_write = provider_probe_module._write_new_private_file
    writes = 0

    def fail_receipt_write(*args: object, **kwargs: object) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("synthetic receipt failure")
        original_write(*args, **kwargs)

    monkeypatch.setattr(provider_probe_module, "_write_new_private_file", fail_receipt_write)

    with pytest.raises(ForeignNewsProviderProbeError) as raised:
        ForeignNewsProviderProbeExecutor(
            control_root=_private_root(tmp_path),
            transport=_PreHandoffFailureTransport(),
            now_provider=lambda: _NOW + timedelta(seconds=2),
        ).execute(
            packet=_packet(),
            binding=_binding(),
            api_key="test-secret-value",
            analyzer=_RecordingAnalyzer(),
            now=_NOW,
            user_agent="test-contact@example.invalid",
        )

    assert raised.value.code == "FOREIGN_NEWS_PROBE_RECEIPT_UNAVAILABLE"
    assert raised.value.physical_call_count == 0
    assert writes == 2


def test_stdlib_transport_keeps_finnhub_key_in_memory_target_only_and_official_requests_keyless() -> None:
    finnhub_connection = _Connection(
        body=b'[{"headline":"profit improved","summary":"guidance raised"}]',
        content_type="application/json",
    )
    transport = StdlibForeignNewsProviderProbeTransport()
    transport._resolver = _StaticResolver()  # type: ignore[assignment]
    transport._transport = _PinnedTransport(finnhub_connection)  # type: ignore[assignment]

    response = transport.get(
        operation="FINNHUB_COMPANY_NEWS",
        hostname="finnhub.io",
        target="/api/v1/company-news?from=2026-08-07&symbol=AAPL&to=2026-08-07",
        api_key="test-secret-value",
        user_agent="test-contact@example.invalid",
        expires_at=_NOW + timedelta(minutes=1),
        timeout_seconds=10.0,
        maximum_response_bytes=262_144,
    )

    assert response.content_type == "application/json"
    assert finnhub_connection.captured["target"] == (
        "/api/v1/company-news?from=2026-08-07&symbol=AAPL&to=2026-08-07&token=test-secret-value"
    )
    assert finnhub_connection.captured["headers"] == {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Connection": "close",
        "Host": "finnhub.io",
        "User-Agent": "test-contact@example.invalid",
    }

    sec_connection = _Connection(
        body=b"<html><body>official release</body></html>",
        content_type="text/html",
    )
    transport._transport = _PinnedTransport(sec_connection)  # type: ignore[assignment]
    transport.get(
        operation="SEC_OFFICIAL_RELEASES",
        hostname="www.sec.gov",
        target="/newsroom/press-releases",
        api_key=None,
        user_agent="test-contact@example.invalid",
        expires_at=_NOW + timedelta(minutes=1),
        timeout_seconds=10.0,
        maximum_response_bytes=262_144,
    )
    assert sec_connection.captured["target"] == "/newsroom/press-releases"


def test_stdlib_transport_accepts_the_contract_korean_symbol_alphabet_for_finnhub_company_news() -> None:
    """packet이 허용한 005930.KS가 transport validation에서 다시 거부되면 안 된다."""

    connection = _Connection(
        body=b'[{"headline":"profit improved","summary":"guidance raised"}]',
        content_type="application/json",
    )
    transport = StdlibForeignNewsProviderProbeTransport()
    transport._resolver = _StaticResolver()  # type: ignore[assignment]
    transport._transport = _PinnedTransport(connection)  # type: ignore[assignment]

    response = transport.get(
        operation="FINNHUB_COMPANY_NEWS",
        hostname="finnhub.io",
        target="/api/v1/company-news?from=2026-08-07&symbol=005930.KS&to=2026-08-07",
        api_key="test-secret-value",
        user_agent="test-contact@example.invalid",
        expires_at=_NOW + timedelta(minutes=1),
        timeout_seconds=10.0,
        maximum_response_bytes=262_144,
    )

    assert response.content_type == "application/json"
    assert connection.captured["target"] == (
        "/api/v1/company-news?from=2026-08-07&symbol=005930.KS&to=2026-08-07&token=test-secret-value"
    )


def test_protocol_drift_or_missing_model_gate_never_opens_transport(tmp_path: Path) -> None:
    control_root = _private_root(tmp_path)
    transport = _RecordingTransport(
        response=ForeignNewsProviderProbeHttpResponse(
            status_code=200,
            body=b'[{"headline":"text","summary":"text"}]',
            content_type="application/json",
        ),
    )
    executor = ForeignNewsProviderProbeExecutor(control_root=control_root, transport=transport)

    with pytest.raises(ForeignNewsProviderProbeError, match="FOREIGN_NEWS_PROBE_ANALYZER_UNAVAILABLE"):
        executor.execute(
            packet=_packet(),
            binding=_binding(),
            api_key="test-secret-value",
            analyzer=None,
            now=_NOW,
            user_agent="test-contact@example.invalid",
        )
    assert transport.calls == []
    assert list(control_root.iterdir()) == []

    result = ForeignNewsProviderProbeExecutor(
        control_root=_private_root(tmp_path / "html"),
        transport=_RecordingTransport(
            response=ForeignNewsProviderProbeHttpResponse(
                status_code=200,
                body=b"<!DOCTYPE html><html><body>unsafe entity surface</body></html>",
                content_type="text/html",
            ),
        ),
    ).execute(
        packet=_packet(
            operation="SEC_OFFICIAL_RELEASES",
            provider_family="SEC_OFFICIAL",
            date="NONE",
        ),
        binding=_binding(),
        api_key=None,
        analyzer=_RecordingAnalyzer(),
        now=_NOW,
        user_agent="test-contact@example.invalid",
    )
    assert result.receipt.outcome == "FAILED"
    assert result.receipt.provider_status_class == "PROTOCOL"
