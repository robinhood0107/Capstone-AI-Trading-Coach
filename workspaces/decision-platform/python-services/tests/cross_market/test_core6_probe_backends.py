from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.cross_market.core6_probe import (
    Core6ProbeBackendResult,
    Core6ProbeError,
    Core6ProbePacket,
    core6_endpoint_set_identity_hash,
    core6_request_plan_digest,
)
from app.cross_market.core6_probe_backends import (
    Core6KisCurrentPriceBackend,
    Core6KrxDailyBackend,
    Core6SecEdgarBackend,
    SecEdgarProbeHttpResponse,
    _ProductionKisProbeSession,
    _sec_target,
    StdlibSecEdgarProbeTransport,
    build_core6_backend,
)
from app.data.kis.parsers import CurrentPrice
from app.data.krx.parsers import KrxDailyRow


def test_kis_backend_requires_cached_token_preflight_and_hashes_only_normalized_scalars() -> None:
    session = _KisSession()
    backend = Core6KisCurrentPriceBackend(session_factory=lambda: session)
    packet = _packet("KIS_CURRENT_PRICE", resource_id="005930", date="NONE")

    backend.preflight(packet=packet)
    result = backend.execute(packet=packet)

    assert session.events == ["preflight", "current:005930", "close"]
    assert result.outcome == "SUCCESS"
    assert result.provider_status_class == "HTTP_2XX"
    assert result.physical_call_count == 1
    assert result.projection_hash is not None
    assert len(result.projection_hash) == 64


def test_kis_backend_reports_not_executed_when_cached_token_disappears_before_handoff() -> None:
    session = _KisSession(fail_current=True, physical_call_count=0)
    backend = Core6KisCurrentPriceBackend(session_factory=lambda: session)
    packet = _packet("KIS_CURRENT_PRICE", resource_id="005930", date="NONE")

    backend.preflight(packet=packet)
    result = backend.execute(packet=packet)

    assert result == Core6ProbeBackendResult(
        outcome="NOT_EXECUTED",
        provider_status_class="NOT_ATTEMPTED",
        projection_hash=None,
        physical_call_count=0,
    )


def test_kis_backend_seals_unknown_attempt_when_recorder_fails_after_handoff() -> None:
    session = _KisSession(
        fail_current=True,
        fail_physical_call_count=True,
    )
    backend = Core6KisCurrentPriceBackend(session_factory=lambda: session)
    packet = _packet("KIS_CURRENT_PRICE", resource_id="005930", date="NONE")

    backend.preflight(packet=packet)
    result = backend.execute(packet=packet)

    assert result == Core6ProbeBackendResult(
        outcome="FAILED",
        provider_status_class="TRANSPORT",
        projection_hash=None,
        physical_call_count=1,
    )


def test_production_kis_session_honors_environment_offline_kill_switch(monkeypatch) -> None:
    class _OfflineSettings:
        offline = True

    monkeypatch.setattr(
        "app.cross_market.core6_probe_backends.KISSettings",
        lambda **kwargs: _OfflineSettings(),
    )

    with pytest.raises(Core6ProbeError, match="CORE6_PROBE_KIS_OFFLINE"):
        _ProductionKisProbeSession()


def test_krx_backend_reuses_fixed_service_and_returns_derived_hash_only() -> None:
    session = _KrxSession()
    backend = Core6KrxDailyBackend(session_factory=lambda: session)
    packet = _packet("KRX_KOSPI_DAILY", resource_id="NONE", date="2026-08-07")

    backend.preflight(packet=packet)
    result = backend.execute(packet=packet)

    assert session.events == ["preflight:2026-08-07", "fetch:stk_bydd_trd:2026-08-07", "close"]
    assert result.outcome == "SUCCESS"
    assert result.projection_hash is not None


def test_sec_backend_validates_cik_transiently_without_retaining_response_body() -> None:
    transport = _SecTransport(
        SecEdgarProbeHttpResponse(
            status_code=200,
            body=b'{"cik":"320193","filings":{}}',
            physical_call_count=1,
        )
    )
    backend = Core6SecEdgarBackend(
        transport=transport,
        user_agent_reader=lambda: "Capstone AI Trading Coach support@example.com",
    )
    packet = _packet("SEC_EDGAR_SUBMISSIONS", resource_id="CIK0000320193", date="NONE")

    backend.preflight(packet=packet)
    result = backend.execute(packet=packet)

    assert transport.calls == [("SEC_EDGAR_SUBMISSIONS", "CIK0000320193")]
    assert result.outcome == "SUCCESS"
    assert result.projection_hash is not None
    assert b"320193" not in result.projection_hash.encode("ascii")


def test_sec_backend_rejects_missing_operator_contact_before_transport() -> None:
    transport = _SecTransport(
        SecEdgarProbeHttpResponse(status_code=200, body=b'{"cik":320193}', physical_call_count=1)
    )
    backend = Core6SecEdgarBackend(transport=transport, user_agent_reader=lambda: "missing-contact")

    with pytest.raises(Core6ProbeError, match="CORE6_PROBE_SEC_USER_AGENT_REQUIRED"):
        backend.preflight(packet=_packet("SEC_EDGAR_SUBMISSIONS", resource_id="CIK0000320193", date="NONE"))

    assert transport.calls == []


def test_sec_backend_seals_unknown_transport_exception_as_physical_attempt() -> None:
    backend = Core6SecEdgarBackend(
        transport=_ThrowingSecTransport(),
        user_agent_reader=lambda: "Capstone AI Trading Coach support@example.com",
    )
    packet = _packet("SEC_EDGAR_SUBMISSIONS", resource_id="CIK0000320193", date="NONE")

    backend.preflight(packet=packet)
    result = backend.execute(packet=packet)

    assert result == Core6ProbeBackendResult(
        outcome="FAILED",
        provider_status_class="TRANSPORT",
        projection_hash=None,
        physical_call_count=1,
    )


def test_sec_fixed_target_validates_packet_operation_and_cik_before_transport() -> None:
    assert _sec_target(
        operation="SEC_EDGAR_SUBMISSIONS",
        resource_id="CIK0000320193",
    ) == "/submissions/CIK0000320193.json"
    assert _sec_target(
        operation="SEC_EDGAR_COMPANYFACTS",
        resource_id="CIK0000320193",
    ) == "/api/xbrl/companyfacts/CIK0000320193.json"

    with pytest.raises(Core6ProbeError, match="CORE6_PROBE_RESOURCE_INVALID"):
        _sec_target(operation="SEC_EDGAR_SUBMISSIONS", resource_id="CIK320193")


def test_sec_transport_counts_post_handoff_failure_as_physical_attempt(monkeypatch) -> None:
    """HTTP handoff 뒤 read failure는 request bytes 일부가 나갔을 수 있어 cap을 보수적으로 소비한다."""

    connection = _PostHandoffFailureSecConnection()
    transport = StdlibSecEdgarProbeTransport()
    transport._transport = _PostHandoffFailureSecTransport(connection)  # type: ignore[attr-defined]
    monkeypatch.setattr(
        "app.cross_market.core6_probe_backends._Oa112SourceDeadline",
        _StaticSecDeadline,
    )
    monkeypatch.setattr(
        "app.cross_market.core6_probe_backends._resolve_public_addresses",
        lambda *_args, **_kwargs: ("203.0.113.10",),
    )
    monkeypatch.setattr(
        "app.cross_market.core6_probe_backends._validate_peer",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(Core6ProbeError) as caught:
        transport.get(
            operation="SEC_EDGAR_SUBMISSIONS",
            resource_id="CIK0000320193",
            user_agent="Capstone AI Trading Coach support@example.com",
            expires_at=datetime(2026, 8, 9, 2, tzinfo=UTC),
        )

    assert caught.value.code == "CORE6_PROBE_SEC_TRANSPORT_UNAVAILABLE"
    assert caught.value.physical_call_count == 1
    assert connection.targets == ["/submissions/CIK0000320193.json"]


@pytest.mark.parametrize(
    "operation",
    [
        "KIS_CURRENT_PRICE",
        "SEC_EDGAR_SUBMISSIONS",
        "SEC_EDGAR_COMPANYFACTS",
        "KRX_KOSPI_DAILY",
        "KRX_KOSDAQ_DAILY",
    ],
)
def test_factory_accepts_only_fixed_core6_operation_set(operation: str) -> None:
    assert build_core6_backend(operation=operation) is not None

    with pytest.raises(Core6ProbeError, match="CORE6_PROBE_OPERATION_PROVIDER_INVALID"):
        build_core6_backend(operation="KOFIA_ANYTHING")


class _KisSession:
    def __init__(
        self,
        *,
        fail_current: bool = False,
        fail_physical_call_count: bool = False,
        physical_call_count: int = 1,
    ) -> None:
        self.events: list[str] = []
        self._fail_current = fail_current
        self._fail_physical_call_count = fail_physical_call_count
        self._physical_call_count = physical_call_count

    def preflight(self) -> None:
        self.events.append("preflight")

    def current_price(self, *, symbol: str) -> CurrentPrice:
        self.events.append(f"current:{symbol}")
        if self._fail_current:
            raise RuntimeError("synthetic pre-send failure")
        return CurrentPrice(
            symbol=symbol,
            price=70_000,
            open=69_500,
            high=70_500,
            low=69_000,
            volume=1_000,
            turnover=70_000_000,
            previous_diff=100,
            previous_rate=0,
        )

    def physical_call_count(self) -> int:
        if self._fail_physical_call_count:
            raise RuntimeError("synthetic recorder failure")
        return self._physical_call_count

    def close(self) -> None:
        self.events.append("close")


class _KrxSession:
    def __init__(self) -> None:
        self.events: list[str] = []

    def preflight(self, *, as_of: date) -> None:
        self.events.append(f"preflight:{as_of.isoformat()}")

    def fetch_rows(self, *, as_of: date, service: str) -> tuple[KrxDailyRow, ...]:
        self.events.append(f"fetch:{service}:{as_of.isoformat()}")
        return (
            KrxDailyRow(
                as_of_date=as_of,
                symbol="005930",
                name="ignored-provider-name",
                market="KOSPI",
                trading_value=1_000,
                market_cap=2_000,
            ),
        )

    def physical_call_count(self) -> int:
        return 1

    def close(self) -> None:
        self.events.append("close")


class _SecTransport:
    def __init__(self, response: SecEdgarProbeHttpResponse) -> None:
        self._response = response
        self.calls: list[tuple[str, str]] = []

    def get(
        self,
        *,
        operation: str,
        resource_id: str,
        user_agent: str,
        expires_at: datetime,
    ) -> SecEdgarProbeHttpResponse:
        assert "@" in user_agent
        assert expires_at.tzinfo is not None
        self.calls.append((operation, resource_id))
        return self._response


class _ThrowingSecTransport:
    def get(
        self,
        *,
        operation: str,
        resource_id: str,
        user_agent: str,
        expires_at: datetime,
    ) -> SecEdgarProbeHttpResponse:
        assert operation == "SEC_EDGAR_SUBMISSIONS"
        assert resource_id == "CIK0000320193"
        assert "@" in user_agent
        assert expires_at.tzinfo is not None
        raise RuntimeError("synthetic unknown post-handoff failure")


class _StaticSecDeadline:
    def __init__(self, *, expires_at: datetime) -> None:
        self.expires_at = expires_at

    def __enter__(self) -> _StaticSecDeadline:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def remaining_seconds(self) -> float:
        return 10.0


class _PostHandoffFailureSecConnection:
    peer_ip = "203.0.113.10"

    def __init__(self) -> None:
        self.targets: list[str] = []

    def __enter__(self) -> _PostHandoffFailureSecConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(
        self,
        *,
        target: str,
        headers: dict[str, str],
        read_timeout_seconds: float,
    ) -> object:
        assert headers["Accept"] == "application/json"
        assert read_timeout_seconds > 0
        self.targets.append(target)
        raise OSError("synthetic post-handoff read failure")


class _PostHandoffFailureSecTransport:
    def __init__(self, connection: _PostHandoffFailureSecConnection) -> None:
        self._connection = connection

    def connect(self, **_kwargs: object) -> _PostHandoffFailureSecConnection:
        return self._connection


def _packet(operation: str, *, resource_id: str, date: str) -> Core6ProbePacket:
    now = datetime(2026, 8, 9, 1, tzinfo=UTC)
    family = (
        "KIS"
        if operation == "KIS_CURRENT_PRICE"
        else "SEC_EDGAR"
        if operation.startswith("SEC_")
        else "KRX"
    )
    return Core6ProbePacket(
        approval_id="c6p_0123456789abcdef0123456789abcdef",
        ci_digest="a" * 64,
        cost_cap_microusd=0,
        date=date,
        endpoint_set_identity_hash=core6_endpoint_set_identity_hash(family),
        expires_at=now + timedelta(minutes=30),
        head_sha="b" * 40,
        logical_call_cap=1,
        nonce="core6-probe-nonce-0001",
        operation=operation,
        operator="local-operator",
        physical_call_cap=1,
        provider_family=family,
        request_plan_digest=core6_request_plan_digest(
            operation=operation,
            resource_id=resource_id,
            date=date,
        ),
        resource_id=resource_id,
        retry_count=0,
        security_digest="c" * 64,
        tracked_raw_artifact_count=0,
        tree_sha256="d" * 64,
    )
