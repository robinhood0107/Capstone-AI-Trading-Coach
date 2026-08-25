"""Sequential, packet-gated KRX/KIS/ECOS read-only smoke verification."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Protocol, cast
from uuid import uuid4

from app.verification.artifacts import claim_packet_execution
from app.verification.models import (
    PROVIDER_READ_SMOKE_GATE_ORDER,
    AggregateOutcome,
    ExecutionState,
    GateResult,
    VerificationReport,
)
from app.verification.packet import (
    P1SignedApprovalPacket,
    VerificationPacketError,
    VerificationTarget,
)
from app.verification.provider_claim import claim_signed_provider_approval

OPERATION_ORDER: Final[tuple[str, ...]] = PROVIDER_READ_SMOKE_GATE_ORDER
_KRX_SERVICE: Final[Mapping[str, str]] = {
    "KRX_KOSPI_DAILY": "stk_bydd_trd",
    "KRX_KOSDAQ_DAILY": "ksq_bydd_trd",
}
_ECOS_SERIES_INDEX: Final[Mapping[str, int]] = {
    "ECOS_POLICY_RATE_DAILY": 0,
    "ECOS_KRW_USD_DAILY": 1,
}


@dataclass(frozen=True, slots=True)
class _ProviderDependencies:
    canonical_json_sha256: Callable[[object], str]
    ecos_http_client: type[Any]
    ecos_series: tuple[Any, ...]
    ecos_settings: type[Any]
    collection_run_recorder: type[Any]
    collection_run_status: Any
    logical_operation: Any
    physical_channel: Any
    kis_http_client: type[Any]
    kis_market_client: type[Any]
    kis_settings: type[Any]
    enabled_universe_endpoints_by_service: Mapping[str, Any]
    krx_open_api_client: type[Any]
    attest_quota_backend_credentials: Callable[[], None]
    is_kis_compatible_symbol: Callable[[str], bool]
    krx_open_api_settings: type[Any]


def _load_provider_dependencies() -> _ProviderDependencies:
    """Load credential-capable transports only after binding and one-shot claims."""

    from app.data._shared.canonical_json import canonical_json_sha256
    from app.data.ecos.http_client import ECOSHttpClient
    from app.data.ecos.series_registry import CANDIDATE_SERIES
    from app.data.ecos.settings import ECOSSettings
    from app.data.kis.accounting import (
        CollectionRunRecorder,
        CollectionRunStatus,
        LogicalOperation,
        PhysicalChannel,
    )
    from app.data.kis.http_client import KISHttpClient
    from app.data.kis.market_client import KISMarketClient
    from app.data.kis.settings import KISSettings
    from app.data.krx.catalog import ENABLED_UNIVERSE_ENDPOINTS_BY_SERVICE
    from app.data.krx.client import KrxOpenApiClient, attest_quota_backend_credentials
    from app.data.krx.parsers import is_kis_compatible_symbol
    from app.data.krx.settings import KrxOpenApiSettings

    return _ProviderDependencies(
        canonical_json_sha256=canonical_json_sha256,
        ecos_http_client=ECOSHttpClient,
        ecos_series=CANDIDATE_SERIES,
        ecos_settings=ECOSSettings,
        collection_run_recorder=CollectionRunRecorder,
        collection_run_status=CollectionRunStatus,
        logical_operation=LogicalOperation,
        physical_channel=PhysicalChannel,
        kis_http_client=KISHttpClient,
        kis_market_client=KISMarketClient,
        kis_settings=KISSettings,
        enabled_universe_endpoints_by_service=ENABLED_UNIVERSE_ENDPOINTS_BY_SERVICE,
        krx_open_api_client=KrxOpenApiClient,
        attest_quota_backend_credentials=attest_quota_backend_credentials,
        is_kis_compatible_symbol=is_kis_compatible_symbol,
        krx_open_api_settings=KrxOpenApiSettings,
    )


class ProviderSmokeError(RuntimeError):
    """Stable provider-smoke failure without provider-controlled content."""


@dataclass(frozen=True, slots=True)
class OperationResult:
    physical_call_count: int
    evidence_sha256: str


class ProviderSmokeBackend(Protocol):
    @property
    def provider_data_physical_calls(self) -> int: ...

    @property
    def kis_token_physical_calls(self) -> int: ...

    def preflight(self) -> None: ...

    def execute(self, operation: str, target: VerificationTarget) -> OperationResult: ...

    def close(self) -> None: ...


class ProductionProviderSmokeBackend:
    """Reuse the production transports/parsers while exposing only bounded evidence hashes."""

    def __init__(self, packet: P1SignedApprovalPacket) -> None:
        self._dependencies = _load_provider_dependencies()
        self._packet = packet
        self._provider_calls = 0
        self._kis_token_calls = 0
        self._kis_recorder: Any | None = None
        self._kis_client: Any | None = None
        self._kis_market: Any | None = None

    @property
    def provider_data_physical_calls(self) -> int:
        return self._provider_calls

    @property
    def kis_token_physical_calls(self) -> int:
        self._refresh_kis_counts()
        return self._kis_token_calls

    def preflight(self) -> None:
        # All three provider families use the same authenticated Redis quota boundary.
        self._dependencies.attest_quota_backend_credentials()

    def execute(self, operation: str, target: VerificationTarget) -> OperationResult:
        self._require_packet_current()
        if operation in _KRX_SERVICE:
            return self._execute_krx(operation, target)
        if operation == "KIS_CURRENT_PRICE":
            return self._execute_kis_current(target)
        if operation == "KIS_DAILY_BAR":
            return self._execute_kis_daily(target)
        if operation in _ECOS_SERIES_INDEX:
            return self._execute_ecos(operation, target)
        raise ProviderSmokeError("P1_PROVIDER_OPERATION_NOT_ALLOWED")

    def close(self) -> None:
        market = self._kis_market
        self._kis_market = None
        self._kis_client = None
        if market is not None:
            market.close()

    def _execute_krx(self, operation: str, target: VerificationTarget) -> OperationResult:
        dependencies = self._dependencies
        service = _KRX_SERVICE[operation]
        client = dependencies.krx_open_api_client(
            dependencies.krx_open_api_settings(
                max_calls_per_run=1,
                max_attempts_per_request=1,
                logical_deadline_seconds=130.0,
            )
        )
        before = self._provider_calls
        try:
            rows = client.fetch_service_rows(
                target.session_date,
                service=service,
                deadline_monotonic=_packet_deadline(self._packet),
            )
            expected_market = dependencies.enabled_universe_endpoints_by_service[service].market
            if not rows or any(
                row.as_of_date != target.session_date or row.market != expected_market
                for row in rows
            ):
                raise ProviderSmokeError("P1_KRX_PROJECTION_INVALID")
            positive = sum(
                1
                for row in rows
                if dependencies.is_kis_compatible_symbol(row.symbol)
                and row.market_cap > 0
                and row.trading_value > 0
            )
            if positive <= 0:
                raise ProviderSmokeError("P1_KRX_POSITIVE_CANDIDATE_MISSING")
            evidence = {
                "operation": operation,
                "positiveCandidateCount": positive,
                "projectionSha256": dependencies.canonical_json_sha256(
                    [
                        {
                            "date": row.as_of_date.isoformat(),
                            "market": row.market,
                            "marketCap": row.market_cap,
                            "symbol": row.symbol,
                            "tradingValue": row.trading_value,
                        }
                        for row in sorted(rows, key=lambda value: value.symbol)
                    ]
                ),
                "rowCount": len(rows),
                "sessionDate": target.session_date.isoformat(),
            }
        finally:
            self._provider_calls += client.physical_attempt_count
            client.close()
        return OperationResult(
            physical_call_count=self._provider_calls - before,
            evidence_sha256=dependencies.canonical_json_sha256(evidence),
        )

    def _execute_kis_current(self, target: VerificationTarget) -> OperationResult:
        dependencies = self._dependencies
        market = self._ensure_kis()
        before = self._provider_calls
        try:
            current = market.current_price(target.symbol)
            if current.symbol != target.symbol or current.price <= 0:
                raise ProviderSmokeError("P1_KIS_CURRENT_PRICE_INVALID")
            evidence = {
                "operation": "KIS_CURRENT_PRICE",
                "projectionSha256": dependencies.canonical_json_sha256(
                    {
                        "price": current.price,
                        "symbol": current.symbol,
                    }
                ),
                "rowCount": 1,
                "symbol": target.symbol,
            }
        finally:
            self._refresh_kis_counts()
        return OperationResult(
            physical_call_count=self._provider_calls - before,
            evidence_sha256=dependencies.canonical_json_sha256(evidence),
        )

    def _execute_kis_daily(self, target: VerificationTarget) -> OperationResult:
        dependencies = self._dependencies
        market = self._ensure_kis()
        before = self._provider_calls
        try:
            bars = market.daily_bars(target.symbol, target.session_date, target.session_date)
            if (
                len(bars) != 1
                or bars[0].symbol != target.symbol
                or bars[0].date != target.session_date
                or bars[0].close <= 0
            ):
                raise ProviderSmokeError("P1_KIS_DAILY_BAR_INVALID")
            bar = bars[0]
            evidence = {
                "operation": "KIS_DAILY_BAR",
                "projectionSha256": dependencies.canonical_json_sha256(
                    {
                        "close": bar.close,
                        "date": bar.date.isoformat(),
                        "symbol": bar.symbol,
                    }
                ),
                "rowCount": 1,
                "sessionDate": target.session_date.isoformat(),
                "symbol": target.symbol,
            }
        finally:
            self._refresh_kis_counts()
        return OperationResult(
            physical_call_count=self._provider_calls - before,
            evidence_sha256=dependencies.canonical_json_sha256(evidence),
        )

    def _execute_ecos(self, operation: str, target: VerificationTarget) -> OperationResult:
        dependencies = self._dependencies
        series = dependencies.ecos_series[_ECOS_SERIES_INDEX[operation]]
        start = target.session_date - timedelta(days=29)
        client = dependencies.ecos_http_client(
            dependencies.ecos_settings(max_calls_per_run=1, max_attempts_per_request=1),
            approval_deadline_monotonic=_packet_deadline(self._packet),
        )
        before = self._provider_calls
        try:
            page = client.statistic_search(
                series=series,
                start=start,
                end=target.session_date,
                page_start=1,
                page_end=200,
            )
            if page.status != "complete" or not page.observations or page.total_count <= 0:
                raise ProviderSmokeError("P1_ECOS_SERIES_EMPTY")
            evidence = {
                "cycle": series.cycle,
                "from": start.isoformat(),
                "itemCode": series.item_code1,
                "nameSha256": dependencies.canonical_json_sha256(series.name),
                "operation": operation,
                "projectionSha256": dependencies.canonical_json_sha256(
                    [item.model_dump(mode="json") for item in page.observations]
                ),
                "rowCount": len(page.observations),
                "statCode": series.stat_code,
                "to": target.session_date.isoformat(),
                "unitSha256": dependencies.canonical_json_sha256(series.unit),
            }
        finally:
            self._provider_calls += client.physical_attempt_count
            client.close()
        return OperationResult(
            physical_call_count=self._provider_calls - before,
            evidence_sha256=dependencies.canonical_json_sha256(evidence),
        )

    def _ensure_kis(self) -> Any:
        if self._kis_market is not None:
            return self._kis_market
        dependencies = self._dependencies
        settings = dependencies.kis_settings(
            kis_mode="live",
            kis_offline=False,
            kis_retry_attempts=1,
        )
        recorder = dependencies.collection_run_recorder(
            run_id=uuid4(),
            started_at=datetime.now(UTC),
            logical_caps={
                dependencies.logical_operation.CURRENT_PRICE: 1,
                dependencies.logical_operation.DAILY_BARS: 1,
                dependencies.logical_operation.HOLIDAY: 0,
            },
            physical_caps={
                dependencies.physical_channel.MARKET_DATA: 2,
                dependencies.physical_channel.TOKEN_P: self._packet.kis_token_physical_call_cap,
            },
        )
        client = dependencies.kis_http_client(
            settings,
            accounting=recorder,
            require_cached_token=self._packet.kis_token_physical_call_cap == 0,
            deadline_guard=self._require_packet_current,
        )
        self._kis_recorder = recorder
        self._kis_client = client
        try:
            if self._packet.kis_token_physical_call_cap == 0:
                client.require_cached_access_token()
            else:
                client.prepare_access_token()
            client.freeze_access_token_refresh()
        except Exception:
            self._refresh_kis_counts()
            client.close()
            self._kis_client = None
            raise
        self._kis_market = dependencies.kis_market_client(settings, client, accounting=recorder)
        self._refresh_kis_counts()
        return self._kis_market

    def _refresh_kis_counts(self) -> None:
        recorder = self._kis_recorder
        if recorder is None:
            return
        summary = recorder.snapshot(
            completed_at=datetime.now(UTC),
            status=self._dependencies.collection_run_status.SUCCESS,
        )
        data_count = next(
            item.attempts
            for item in summary.physical_attempts
            if item.channel == self._dependencies.physical_channel.MARKET_DATA
        )
        token_count = next(
            item.attempts
            for item in summary.physical_attempts
            if item.channel == self._dependencies.physical_channel.TOKEN_P
        )
        non_kis_calls = self._provider_calls - getattr(self, "_last_kis_data_calls", 0)
        self._provider_calls = non_kis_calls + data_count
        self._last_kis_data_calls = data_count
        self._kis_token_calls = token_count

    def _require_packet_current(self) -> None:
        self._packet.validate(now=datetime.now(UTC))


BackendFactory = Callable[[P1SignedApprovalPacket], ProviderSmokeBackend]
BindingVerifier = Callable[[P1SignedApprovalPacket, Path], None]
ClaimFunction = Callable[[Path, P1SignedApprovalPacket], Path]
GlobalClaimFunction = Callable[[P1SignedApprovalPacket], None]


def run_provider_read_smoke(
    *,
    repository_root: Path,
    output_root: Path,
    packet: P1SignedApprovalPacket,
    binding_verifier: BindingVerifier,
    backend_factory: BackendFactory = ProductionProviderSmokeBackend,
    claim: ClaimFunction | None = None,
    global_claim: GlobalClaimFunction = claim_signed_provider_approval,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> VerificationReport:
    """Run exactly six sequential reads; the first terminal failure stops the rest."""

    if not isinstance(packet, P1SignedApprovalPacket):
        raise VerificationPacketError("P1 verification packet v1 has no execution authority")
    started_at = now().astimezone(UTC)
    binding_verifier(packet, repository_root.resolve(strict=True))
    global_claim(packet)
    claim_call = claim or (
        lambda root, value: claim_packet_execution(root, value, claimed_at=started_at)
    )
    claim_call(output_root, packet)

    backend: ProviderSmokeBackend | None = None
    gates: list[GateResult] = []
    try:
        try:
            backend = backend_factory(packet)
            backend.preflight()
        except Exception as error:
            gates.append(_blocked_gate(OPERATION_ORDER[0], error))
            gates.extend(_not_run_gate(operation) for operation in OPERATION_ORDER[1:])
            return _provider_report(
                packet=packet,
                started_at=started_at,
                completed_at=now().astimezone(UTC),
                gates=tuple(gates),
                execution_state="BLOCKED",
                provider_calls=0 if backend is None else backend.provider_data_physical_calls,
                token_calls=0 if backend is None else backend.kis_token_physical_calls,
            )

        failed = False
        for operation in OPERATION_ORDER:
            if failed:
                gates.append(_not_run_gate(operation))
                continue
            before = backend.provider_data_physical_calls
            try:
                result = backend.execute(operation, packet.target)
                observed = backend.provider_data_physical_calls - before
                if result.physical_call_count != 1 or observed != 1:
                    raise ProviderSmokeError("P1_PHYSICAL_ATTEMPT_ACCOUNTING_INVALID")
            except Exception as error:
                observed = backend.provider_data_physical_calls - before
                gates.append(_failed_gate(operation, error, observed))
                failed = True
            else:
                gates.append(
                    GateResult(
                        gate_id=operation,
                        required=True,
                        implementation_state="IMPLEMENTED",
                        execution_state="PASS",
                        physical_call_count=1,
                        evidence_sha256=result.evidence_sha256,
                        failure_code=None,
                    )
                )

        provider_calls = backend.provider_data_physical_calls
        token_calls = backend.kis_token_physical_calls
        if provider_calls > 6 or token_calls > packet.kis_token_physical_call_cap:
            raise ProviderSmokeError("P1_TOTAL_PHYSICAL_CAP_EXCEEDED")
        passed = not failed and provider_calls == 6
        return _provider_report(
            packet=packet,
            started_at=started_at,
            completed_at=now().astimezone(UTC),
            gates=tuple(gates),
            execution_state="PASS" if passed else "FAIL",
            provider_calls=provider_calls,
            token_calls=token_calls,
        )
    finally:
        if backend is not None:
            backend.close()


def _provider_report(
    *,
    packet: P1SignedApprovalPacket,
    started_at: datetime,
    completed_at: datetime,
    gates: tuple[GateResult, ...],
    execution_state: str,
    provider_calls: int,
    token_calls: int,
) -> VerificationReport:
    outcome = "PASS" if execution_state == "PASS" else execution_state
    report = VerificationReport(
        run_id=f"p1v1-{started_at.strftime('%Y%m%dt%H%M%S')}-{packet.head_sha[:8]}",
        profile="PROVIDER_READ_SMOKE",
        head_sha=packet.head_sha,
        started_at=started_at,
        completed_at=completed_at,
        implementation_state="IMPLEMENTED",
        execution_state=cast(ExecutionState, execution_state),
        aggregate_outcome=cast(AggregateOutcome, outcome),
        gates=gates,
        provider_data_physical_calls=provider_calls,
        kis_token_physical_calls=token_calls,
        packet_sha256=packet.packet_sha256,
    )
    report.validate()
    return report


def _blocked_gate(operation: str, error: BaseException) -> GateResult:
    return GateResult(
        gate_id=operation,
        required=True,
        implementation_state="IMPLEMENTED",
        execution_state="BLOCKED",
        physical_call_count=0,
        evidence_sha256=None,
        failure_code=_stable_failure_code(error),
    )


def _failed_gate(operation: str, error: BaseException, calls: int) -> GateResult:
    return GateResult(
        gate_id=operation,
        required=True,
        implementation_state="IMPLEMENTED",
        execution_state="FAIL",
        physical_call_count=max(0, calls),
        evidence_sha256=None,
        failure_code=_stable_failure_code(error),
    )


def _not_run_gate(operation: str) -> GateResult:
    return GateResult(
        gate_id=operation,
        required=True,
        implementation_state="IMPLEMENTED",
        execution_state="NOT_RUN",
        physical_call_count=0,
        evidence_sha256=None,
        failure_code=None,
    )


def _stable_failure_code(error: BaseException) -> str:
    if isinstance(error, ProviderSmokeError):
        return str(error)
    if isinstance(error, VerificationPacketError):
        return "P1_PACKET_INVALID"
    name = type(error).__name__
    allowlisted = {
        "ECOSCredentialError": "P1_ECOS_CREDENTIAL_BLOCKED",
        "ECOSHttpError": "P1_ECOS_PROVIDER_FAILED",
        "KISCallBudgetExceeded": "P1_KIS_BUDGET_EXHAUSTED",
        "KISCredentialError": "P1_KIS_CREDENTIAL_BLOCKED",
        "KISHttpError": "P1_KIS_PROVIDER_FAILED",
        "KISResponseError": "P1_KIS_PARSE_FAILED",
        "KrxCredentialError": "P1_KRX_CREDENTIAL_BLOCKED",
        "KrxHttpError": "P1_KRX_PROVIDER_FAILED",
        "QuotaUnavailableError": "P1_QUOTA_PREFLIGHT_BLOCKED",
    }
    return allowlisted.get(name, "P1_PROVIDER_INTERNAL_FAILED")


def _packet_deadline(packet: P1SignedApprovalPacket) -> float:
    import time

    remaining = (packet.expires_at - datetime.now(UTC)).total_seconds()
    if remaining <= 0:
        raise VerificationPacketError("P1 verification packet expired")
    return time.monotonic() + remaining
