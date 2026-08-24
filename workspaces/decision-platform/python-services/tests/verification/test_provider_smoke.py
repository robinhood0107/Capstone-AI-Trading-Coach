from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.data.ecos.models import ECOSObservation, StatisticSearchPage
from app.data.kis.accounting import PhysicalChannel
from app.data.krx.catalog import ENABLED_UNIVERSE_ENDPOINTS_BY_SERVICE
from app.data.krx.parsers import KrxDailyRow
from app.verification.artifacts import (
    VerificationArtifactError,
    claim_packet_execution,
)
from app.verification.models import VerificationReport
from app.verification.packet import (
    LIVE_OPERATIONS,
    P1SignedApprovalPacket,
    P1VerificationPacket,
    VerificationPacketError,
    VerificationTarget,
)
from app.verification.provider_smoke import (
    OPERATION_ORDER,
    OperationResult,
    ProductionProviderSmokeBackend,
    ProviderSmokeError,
    run_provider_read_smoke,
)


class _FakeBackend:
    def __init__(
        self,
        *,
        fail_at: str | None = None,
        preflight_error: BaseException | None = None,
        token_calls: int = 0,
    ) -> None:
        self.fail_at = fail_at
        self.preflight_error = preflight_error
        self.provider_data_physical_calls = 0
        self.kis_token_physical_calls = token_calls
        self.operations: list[str] = []
        self.targets: list[VerificationTarget] = []
        self.closed = False

    def preflight(self) -> None:
        if self.preflight_error is not None:
            raise self.preflight_error

    def execute(self, operation: str, target: VerificationTarget) -> OperationResult:
        assert target.symbol == "005930"
        self.targets.append(target)
        self.operations.append(operation)
        self.provider_data_physical_calls += 1
        if operation == self.fail_at:
            raise ProviderSmokeError("P1_FIXTURE_TERMINAL_FAILURE")
        return OperationResult(1, canonical_evidence(operation))

    def close(self) -> None:
        self.closed = True


def canonical_evidence(operation: str) -> str:
    from app.data._shared.canonical_json import canonical_json_sha256

    return canonical_json_sha256({"operation": operation, "rowCount": 1})


def _packet(*, token_cap: int = 1) -> P1SignedApprovalPacket:
    now = datetime(2026, 8, 21, 0, tzinfo=UTC)
    return P1SignedApprovalPacket(
        approval_id="P1.V1-20260821-READ-SMOKE",
        nonce="e" * 32,
        issuer_key_id="P1.TEST",
        allowed_operations=LIVE_OPERATIONS,
        physical_call_cap=len(LIVE_OPERATIONS) + token_cap,
        head_sha="a" * 40,
        tree_sha256="b" * 64,
        target=VerificationTarget(date(2026, 8, 20)),
        expires_at=now + timedelta(minutes=5),
        reason_code="P1_READ_SMOKE",
        signature="A" * 86,
    )


def _run(tmp_path: Path, backend: _FakeBackend) -> VerificationReport:
    clock = datetime(2026, 8, 21, 0, 30, tzinfo=UTC)
    return run_provider_read_smoke(
        repository_root=tmp_path,
        output_root=tmp_path / "artifacts",
        packet=_packet(token_cap=backend.kis_token_physical_calls),
        backend_factory=lambda _: backend,
        binding_verifier=lambda *_: None,
        claim=lambda *_: tmp_path / "claim.json",
        global_claim=lambda *_: None,
        now=lambda: clock,
    )


def test_provider_smoke_passes_only_six_single_physical_attempts(tmp_path: Path) -> None:
    backend = _FakeBackend(token_calls=1)

    report = _run(tmp_path, backend)

    assert report.execution_state == "PASS"
    assert report.aggregate_outcome == "PASS"
    assert tuple(gate.gate_id for gate in report.gates) == OPERATION_ORDER
    assert all(gate.execution_state == "PASS" for gate in report.gates)
    assert report.provider_data_physical_calls == 6
    assert report.kis_token_physical_calls == 1
    assert report.account_calls == report.balance_calls == report.order_calls == 0
    assert report.product_db_writes == 0
    assert backend.operations == list(OPERATION_ORDER)
    assert len(backend.targets) == 6
    assert set(backend.targets) == {VerificationTarget(date(2026, 8, 20))}
    assert backend.closed
    assert VerificationReport.from_dict(report.to_dict()) == report


def test_provider_smoke_stops_all_later_operations_after_first_failure(tmp_path: Path) -> None:
    backend = _FakeBackend(fail_at="KIS_DAILY_BAR")

    report = _run(tmp_path, backend)

    assert report.execution_state == "FAIL"
    assert backend.operations == list(OPERATION_ORDER[:4])
    assert [gate.execution_state for gate in report.gates] == [
        "PASS",
        "PASS",
        "PASS",
        "FAIL",
        "NOT_RUN",
        "NOT_RUN",
    ]
    assert report.gates[3].failure_code == "P1_FIXTURE_TERMINAL_FAILURE"
    assert report.provider_data_physical_calls == 4


def test_provider_smoke_preflight_block_has_zero_provider_calls(tmp_path: Path) -> None:
    backend = _FakeBackend(preflight_error=RuntimeError("untrusted detail"))

    report = _run(tmp_path, backend)

    assert report.execution_state == "BLOCKED"
    assert report.aggregate_outcome == "BLOCKED"
    assert report.provider_data_physical_calls == 0
    assert report.gates[0].execution_state == "BLOCKED"
    assert report.gates[0].failure_code == "P1_PROVIDER_INTERNAL_FAILED"
    assert all(gate.execution_state == "NOT_RUN" for gate in report.gates[1:])
    assert backend.operations == []
    assert backend.closed


def test_repository_binding_and_claim_precede_backend_construction(tmp_path: Path) -> None:
    events: list[str] = []
    backend = _FakeBackend()

    run_provider_read_smoke(
        repository_root=tmp_path,
        output_root=tmp_path / "artifacts",
        packet=_packet(token_cap=0),
        backend_factory=lambda _: events.append("backend") or backend,
        binding_verifier=lambda *_: events.append("binding"),
        claim=lambda *_: events.append("claim") or tmp_path / "claim.json",
        global_claim=lambda *_: events.append("global-claim"),
        now=lambda: datetime(2026, 8, 21, 0, 30, tzinfo=UTC),
    )

    assert events == ["binding", "global-claim", "claim", "backend"]


def test_packet_execution_claim_is_not_idempotently_reusable(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    packet = _packet()
    claimed_at = datetime(2026, 8, 21, 0, 30, tzinfo=UTC)

    artifact = claim_packet_execution(root, packet, claimed_at=claimed_at)

    assert artifact.stat().st_mode & 0o777 == 0o600
    assert packet.packet_sha256 in artifact.name
    with pytest.raises(VerificationArtifactError, match="already claimed"):
        claim_packet_execution(root, packet, claimed_at=claimed_at)


def test_production_backend_composes_existing_typed_clients_with_retry_zero(monkeypatch) -> None:
    import app.verification.provider_smoke as smoke

    observed_settings: dict[str, list[object]] = {"krx": [], "kis": [], "ecos": []}

    class FakeKrxClient:
        def __init__(self, settings) -> None:
            observed_settings["krx"].append(settings)
            self.physical_attempt_count = 0

        def fetch_service_rows(self, as_of, *, service, deadline_monotonic):
            assert deadline_monotonic > 0
            self.physical_attempt_count = 1
            market = ENABLED_UNIVERSE_ENDPOINTS_BY_SERVICE[service].market
            return (
                KrxDailyRow(
                    as_of_date=as_of,
                    symbol="005930",
                    name="fixture",
                    market=market,
                    trading_value=1,
                    market_cap=1,
                ),
            )

        def close(self) -> None:
            return None

    class FakeKisHttpClient:
        def __init__(self, settings, **kwargs) -> None:
            observed_settings["kis"].append(settings)
            self.accounting = kwargs["accounting"]

        def prepare_access_token(self) -> None:
            self.accounting.record_physical_attempt(PhysicalChannel.TOKEN_P)
            self.accounting.record_physical_success(PhysicalChannel.TOKEN_P)

        def require_cached_access_token(self) -> None:
            return None

        def freeze_access_token_refresh(self) -> None:
            return None

        def request(self, method, path, tr_id, params=None):
            self.accounting.record_physical_attempt(PhysicalChannel.MARKET_DATA)
            self.accounting.record_physical_success(PhysicalChannel.MARKET_DATA)
            if "daily-itemchartprice" in path:
                return {
                    "rt_cd": "0",
                    "output2": [
                        {
                            "stck_bsop_date": "20260820",
                            "stck_oprc": "100",
                            "stck_hgpr": "110",
                            "stck_lwpr": "90",
                            "stck_clpr": "105",
                            "acml_vol": "10",
                        }
                    ],
                }
            return {
                "rt_cd": "0",
                "output": {
                    "stck_shrn_iscd": "005930",
                    "stck_prpr": "105",
                    "stck_oprc": "100",
                    "stck_hgpr": "110",
                    "stck_lwpr": "90",
                    "acml_vol": "10",
                    "acml_tr_pbmn": "1000",
                    "prdy_vrss": "1",
                    "prdy_ctrt": str(Decimal("1")),
                },
            }

        def close(self) -> None:
            return None

    class FakeEcosClient:
        def __init__(self, settings, **kwargs) -> None:
            observed_settings["ecos"].append(settings)
            assert kwargs["approval_deadline_monotonic"] > 0
            self.physical_attempt_count = 0

        def statistic_search(self, **kwargs):
            self.physical_attempt_count = 1
            return StatisticSearchPage(
                status="complete",
                total_count=1,
                observations=[ECOSObservation(time="20260820", value="1")],
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(smoke, "attest_quota_backend_credentials", lambda: None)
    monkeypatch.setattr(smoke, "KrxOpenApiClient", FakeKrxClient)
    monkeypatch.setattr(smoke, "KISHttpClient", FakeKisHttpClient)
    monkeypatch.setattr(smoke, "ECOSHttpClient", FakeEcosClient)
    now = datetime.now(UTC)
    packet = P1SignedApprovalPacket(
        approval_id="P1.V1-20260821-READ-SMOKE",
        nonce="e" * 32,
        issuer_key_id="P1.TEST",
        allowed_operations=LIVE_OPERATIONS,
        physical_call_cap=len(LIVE_OPERATIONS) + 1,
        head_sha="a" * 40,
        tree_sha256="b" * 64,
        target=VerificationTarget(date(2026, 8, 20)),
        expires_at=now + timedelta(minutes=4),
        reason_code="P1_READ_SMOKE",
        signature="A" * 86,
    )
    backend = ProductionProviderSmokeBackend(packet)

    backend.preflight()
    results = [backend.execute(operation, packet.target) for operation in OPERATION_ORDER]
    backend.close()

    assert all(result.physical_call_count == 1 for result in results)
    assert backend.provider_data_physical_calls == 6
    assert backend.kis_token_physical_calls == 1
    assert len(observed_settings["krx"]) == 2
    assert all(settings.max_attempts_per_request == 1 for settings in observed_settings["krx"])
    assert observed_settings["kis"][0].kis_retry_attempts == 1
    assert len(observed_settings["ecos"]) == 2
    assert all(settings.max_attempts_per_request == 1 for settings in observed_settings["ecos"])


def test_legacy_unsigned_packet_has_no_execution_authority(tmp_path: Path) -> None:
    now = datetime(2026, 8, 21, 0, tzinfo=UTC)
    legacy = P1VerificationPacket(
        approval_id="P1.V1-20260821-READ-SMOKE",
        issued_at=now,
        expires_at=now + timedelta(minutes=60),
        head_sha="a" * 40,
        tree_sha256="b" * 64,
        uv_lock_sha256="c" * 64,
        contract_catalog_sha256="d" * 64,
        target=VerificationTarget(date(2026, 8, 20)),
        kis_token_physical_call_cap=0,
    )

    with pytest.raises(VerificationPacketError, match="v1 has no execution authority"):
        run_provider_read_smoke(  # type: ignore[arg-type]
            repository_root=tmp_path,
            output_root=tmp_path / "artifacts",
            packet=legacy,
            binding_verifier=lambda *_: None,
            backend_factory=lambda _: _FakeBackend(),
        )


def test_provider_smoke_has_no_direct_transport_db_or_brokerage_authority() -> None:
    import inspect

    import app.verification.provider_smoke as smoke

    source = inspect.getsource(smoke)
    assert "import httpx" not in source
    assert "import requests" not in source
    assert "import socket" not in source
    assert "production_db" not in source
    assert "account_no" not in source.lower()
    assert "order_client" not in source.lower()
