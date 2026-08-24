from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.brokerage import kis_mock_approval_probe as probe


@pytest.fixture(autouse=True)
def clean_repository_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> object:
    """기존 packet 단위 테스트는 clean-tree 검사를 격리하고 전용 테스트만 실제 Git을 사용한다."""

    original = getattr(probe, "_require_clean_repository", None)
    monkeypatch.setattr(
        probe,
        "_require_clean_repository",
        lambda _repository_root: None,
        raising=False,
    )
    return original


@pytest.fixture
def secure_tmp_path() -> Path:
    path = Path(tempfile.mkdtemp(prefix="s3-online-probe-test-", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path)


class FakeOperations:
    def __init__(
        self,
        *,
        fail_at: str | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.fail_at = fail_at
        self.failure = failure
        self.calls: list[str] = []
        self.closed = False

    def run(self, operation: str, packet: probe.KISMockApprovalPacket) -> None:
        self.calls.append(operation)
        if operation == self.fail_at:
            raise self.failure or RuntimeError("synthetic")

    def counts(self) -> dict[str, int]:
        return {"tokenP": 0, "brokerage": len(self.calls)}

    def close(self) -> None:
        self.closed = True


class OnceApprovalConsumer:
    def __init__(self) -> None:
        self.claimed: set[tuple[str, str]] = set()
        self.calls: list[tuple[str, str, datetime]] = []

    def __call__(self, packet: probe.KISMockApprovalPacket, now: datetime) -> None:
        key = (packet.approval_id, packet.packet_sha256)
        self.calls.append((packet.approval_id, packet.packet_sha256, now))
        if key in self.claimed:
            raise probe.KISMockApprovalRejected("approval packet was already consumed")
        self.claimed.add(key)


class FakeRedis:
    def __init__(self, result: bool | None = True, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, str, bool | None, int | None]] = []
        self.closed = False

    def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool | None = None,
        px: int | None = None,
    ) -> bool | None:
        self.calls.append((key, value, nx, px))
        if self.error is not None:
            raise self.error
        return self.result

    def close(self) -> None:
        self.closed = True


def allow_replay_consumer(_packet: probe.KISMockApprovalPacket, _now: datetime) -> None:
    return None


def test_exact_packet_is_consumed_once_before_runtime_factory(
    secure_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_path, packet_sha = _write_packet(secure_tmp_path)
    consumer = OnceApprovalConsumer()
    runtime_builds = 0
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)

    def factory(_packet: probe.KISMockApprovalPacket) -> FakeOperations:
        nonlocal runtime_builds
        runtime_builds += 1
        return FakeOperations()

    summary = probe.execute_approved_probe(
        packet_path,
        now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
        expected_approval_id="approval-s3-online-test",
        expected_packet_sha256=packet_sha,
        repository_root=secure_tmp_path,
        operations_factory=factory,
        approval_consumer=consumer,
    )

    assert summary.physical_reservations == {"tokenP": 0, "brokerage": 5}
    assert runtime_builds == 1

    with pytest.raises(probe.KISMockApprovalRejected, match="consumed"):
        probe.execute_approved_probe(
            packet_path,
            now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
            expected_approval_id="approval-s3-online-test",
            expected_packet_sha256=packet_sha,
            repository_root=secure_tmp_path,
            operations_factory=factory,
            approval_consumer=consumer,
        )

    assert runtime_builds == 1
    assert len(consumer.calls) == 2


def test_dirty_repository_is_rejected_before_approval_consumption_or_runtime(
    secure_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_path, packet_sha = _write_packet(secure_tmp_path)
    consumed = False
    built = False
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)

    def reject_dirty(_repository_root: Path) -> None:
        raise probe.KISMockApprovalRejected("repository worktree is not clean")

    def consume(_packet: probe.KISMockApprovalPacket, _now: datetime) -> None:
        nonlocal consumed
        consumed = True

    def factory(_packet: probe.KISMockApprovalPacket) -> FakeOperations:
        nonlocal built
        built = True
        return FakeOperations()

    monkeypatch.setattr(probe, "_require_clean_repository", reject_dirty)

    with pytest.raises(probe.KISMockApprovalRejected, match="not clean"):
        probe.execute_approved_probe(
            packet_path,
            now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
            expected_approval_id="approval-s3-online-test",
            expected_packet_sha256=packet_sha,
            repository_root=secure_tmp_path,
            operations_factory=factory,
            approval_consumer=consume,
        )

    assert consumed is False
    assert built is False


def test_clean_repository_guard_ignores_ignored_files_and_rejects_all_dirty_states(
    secure_tmp_path: Path,
    clean_repository_guard: object,
) -> None:
    assert callable(clean_repository_guard)
    repository = secure_tmp_path / "repository"
    repository.mkdir()
    _run_git(repository, "init")
    _run_git(repository, "config", "user.name", "S3 Test")
    _run_git(repository, "config", "user.email", "s3-test@example.invalid")
    (repository / ".gitignore").write_text(".env\nlocal-only-notes/\n", encoding="utf-8")
    tracked = repository / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    _run_git(repository, "add", ".gitignore", "tracked.txt")
    _run_git(repository, "commit", "-m", "test fixture")

    (repository / ".env").write_text("SECRET=ignored\n", encoding="utf-8")
    ignored_notes = repository / "local-only-notes"
    ignored_notes.mkdir()
    (ignored_notes / "note.md").write_text("ignored\n", encoding="utf-8")
    clean_repository_guard(repository)

    untracked = repository / "untracked.txt"
    untracked.write_text("dirty\n", encoding="utf-8")
    with pytest.raises(probe.KISMockApprovalRejected, match="not clean"):
        clean_repository_guard(repository)
    untracked.unlink()

    tracked.write_text("unstaged\n", encoding="utf-8")
    with pytest.raises(probe.KISMockApprovalRejected, match="not clean"):
        clean_repository_guard(repository)

    _run_git(repository, "add", "tracked.txt")
    with pytest.raises(probe.KISMockApprovalRejected, match="not clean"):
        clean_repository_guard(repository)


def test_failed_probe_keeps_exact_packet_consumed(
    secure_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_path, packet_sha = _write_packet(secure_tmp_path)
    consumer = OnceApprovalConsumer()
    failed_operations = FakeOperations(fail_at="submitLimitBuy")
    replay_builds = 0
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)

    with pytest.raises(probe.KISMockProbeFailed):
        probe.execute_approved_probe(
            packet_path,
            now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
            expected_approval_id="approval-s3-online-test",
            expected_packet_sha256=packet_sha,
            repository_root=secure_tmp_path,
            operations_factory=lambda _packet: failed_operations,
            approval_consumer=consumer,
        )

    def replay_factory(_packet: probe.KISMockApprovalPacket) -> FakeOperations:
        nonlocal replay_builds
        replay_builds += 1
        return FakeOperations()

    with pytest.raises(probe.KISMockApprovalRejected, match="consumed"):
        probe.execute_approved_probe(
            packet_path,
            now=datetime(2030, 1, 2, 3, 11, tzinfo=UTC),
            expected_approval_id="approval-s3-online-test",
            expected_packet_sha256=packet_sha,
            repository_root=secure_tmp_path,
            operations_factory=replay_factory,
            approval_consumer=consumer,
        )

    assert failed_operations.calls == ["balance", "buyable", "submitLimitBuy"]
    assert replay_builds == 0


def test_legacy_unsigned_approval_has_no_execution_authority(
    secure_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_path, packet_sha = _write_packet(secure_tmp_path)
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)
    packet = probe._load_packet(
        packet_path,
        now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
        expected_approval_id="approval-s3-online-test",
        expected_packet_sha256=packet_sha,
        repository_root=secure_tmp_path,
    )

    with pytest.raises(probe.KISMockApprovalRejected, match="legacy or unsigned"):
        probe._consume_exact_approval_once(
            packet,
            datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
        )


def test_exact_packet_preflight_rejects_missing_latch_before_runtime_factory(
    secure_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_path, packet_sha = _write_packet(secure_tmp_path)
    built = False

    def factory(packet: probe.KISMockApprovalPacket) -> FakeOperations:
        nonlocal built
        built = True
        return FakeOperations()

    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)

    with pytest.raises(probe.KISMockApprovalRejected, match="approval latch"):
        probe.execute_approved_probe(
            packet_path,
            now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
            expected_approval_id=None,
            expected_packet_sha256=packet_sha,
            repository_root=secure_tmp_path,
            operations_factory=factory,
            approval_consumer=allow_replay_consumer,
        )

    assert built is False


def test_exact_packet_runs_canonical_steps_once_and_closes_runtime(
    secure_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_path, packet_sha = _write_packet(secure_tmp_path)
    operations = FakeOperations()
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)

    summary = probe.execute_approved_probe(
        packet_path,
        now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
        expected_approval_id="approval-s3-online-test",
        expected_packet_sha256=packet_sha,
        repository_root=secure_tmp_path,
        operations_factory=lambda _packet: operations,
        approval_consumer=allow_replay_consumer,
    )

    assert operations.calls == [
        "balance",
        "buyable",
        "submitLimitBuy",
        "cancelFull",
        "executionRead",
    ]
    assert operations.closed is True
    assert summary.completed_steps == tuple(operations.calls)
    assert summary.physical_reservations == {"tokenP": 0, "brokerage": 5}


def test_first_probe_failure_stops_all_remaining_calls(
    secure_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_path, packet_sha = _write_packet(secure_tmp_path)
    operations = FakeOperations(fail_at="submitLimitBuy")
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)

    with pytest.raises(probe.KISMockProbeFailed) as captured:
        probe.execute_approved_probe(
            packet_path,
            now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
            expected_approval_id="approval-s3-online-test",
            expected_packet_sha256=packet_sha,
            repository_root=secure_tmp_path,
            operations_factory=lambda _packet: operations,
            approval_consumer=allow_replay_consumer,
        )

    assert captured.value.failed_step == "submitLimitBuy"
    assert operations.calls == ["balance", "buyable", "submitLimitBuy"]
    assert operations.closed is True


def test_balance_diagnostic_packet_runs_only_one_read(
    secure_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_path, _ = _write_packet(secure_tmp_path)
    _rewrite_packet(packet_path, ("probeType",), "BALANCE_DIAGNOSTIC")
    _rewrite_packet(packet_path, ("steps",), ["balance"])
    packet_sha = _rewrite_packet(packet_path, ("physicalCaps", "brokerage"), 1)
    operations = FakeOperations()
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)

    summary = probe.execute_approved_probe(
        packet_path,
        now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
        expected_approval_id="approval-s3-online-test",
        expected_packet_sha256=packet_sha,
        repository_root=secure_tmp_path,
        operations_factory=lambda _packet: operations,
        approval_consumer=allow_replay_consumer,
    )

    assert operations.calls == ["balance"]
    assert summary.completed_steps == ("balance",)
    assert summary.physical_reservations == {"tokenP": 0, "brokerage": 1}


def test_balance_probe_operation_uses_source_shape_probe_not_complete_balance(
    secure_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_path, packet_sha = _write_packet(secure_tmp_path)
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)
    packet = probe._load_packet(
        packet_path,
        now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
        expected_approval_id="approval-s3-online-test",
        expected_packet_sha256=packet_sha,
        repository_root=secure_tmp_path,
    )
    calls: list[str] = []

    class SourceReader:
        def balance(self, _account_id: str) -> None:
            raise AssertionError("complete balance must not be used by the source probe")

        def probe_balance_source(self, account_id: str) -> object:
            calls.append(account_id)
            return type("Source", (), {"account_id": account_id})()

    operations = object.__new__(probe._KISMockProbeOperations)
    operations._balance_reader = SourceReader()

    operations.run("balance", packet)

    assert calls == [packet.order.account_id]


def test_full_probe_uses_packet_order_division_for_buyable_and_submit(
    secure_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_path, _ = _write_packet(secure_tmp_path)
    packet_sha = _rewrite_packet(packet_path, ("order", "orderDivision"), "07")
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)
    packet = probe._load_packet(
        packet_path,
        now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
        expected_approval_id="approval-s3-online-test",
        expected_packet_sha256=packet_sha,
        repository_root=secure_tmp_path,
    )
    buyable_calls: list[tuple[str, str, int, str]] = []
    submit_order_divisions: list[str | None] = []

    class SourceBalance:
        def buyable(
            self,
            account_id: str,
            symbol: str,
            limit_price_krw: int,
            order_division: str,
        ) -> object:
            buyable_calls.append((account_id, symbol, limit_price_krw, order_division))
            return type(
                "Buyable",
                (),
                {
                    "account_id": account_id,
                    "buyable_quantity": 1,
                    "buyable_amount_krw": limit_price_krw,
                },
            )()

    class SourceGateway:
        def submit_cash_order(
            self,
            intent: object,
            *,
            order_id: str | None = None,
            account_id: str | None = None,
        ) -> object:
            assert order_id == packet.order.order_id
            assert account_id == packet.order.account_id
            submit_order_divisions.append(intent.order_division)
            return type("Receipt", (), {"accepted": True})()

    operations = object.__new__(probe._KISMockProbeOperations)
    operations._balance_reader = SourceBalance()
    operations._gateway = SourceGateway()

    operations.run("buyable", packet)
    operations.run("submitLimitBuy", packet)

    assert buyable_calls == [
        (
            packet.order.account_id,
            packet.order.symbol,
            packet.order.limit_price_krw,
            "07",
        )
    ]
    assert submit_order_divisions == ["07"]


def test_full_probe_rejects_nxt_exchange_division_before_provider(
    secure_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_path, _ = _write_packet(secure_tmp_path)
    _rewrite_packet(packet_path, ("order", "limitPriceKrw"), 220_000)
    _rewrite_packet(packet_path, ("order", "orderDivision"), "00")
    packet_sha = _rewrite_packet(packet_path, ("order", "exchangeDivision"), "NXT")
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)

    with pytest.raises(probe.KISMockApprovalRejected):
        probe._load_packet(
            packet_path,
            now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
            expected_approval_id="approval-s3-online-test",
            expected_packet_sha256=packet_sha,
            repository_root=secure_tmp_path,
        )


def test_execution_probe_operation_uses_source_shape_probe_not_strict_reconciliation(
    secure_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_path, packet_sha = _write_packet(secure_tmp_path)
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)
    packet = probe._load_packet(
        packet_path,
        now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
        expected_approval_id="approval-s3-online-test",
        expected_packet_sha256=packet_sha,
        repository_root=secure_tmp_path,
    )
    reference = object()
    calls: list[tuple[object, object, object, bool]] = []

    class SourceExecutionReader:
        def read(self, **_kwargs: object) -> None:
            raise AssertionError("strict execution reconciliation must not gate probe")

        def probe_execution_source(self, **kwargs: object) -> object:
            calls.append(
                (
                    kwargs["reference"],
                    kwargs["start"],
                    kwargs["end"],
                    kwargs["recent"],
                )
            )
            return object()

    class SourceReferenceStore:
        def get(self, order_id: str, account_id: str) -> object | None:
            assert order_id == packet.order.order_id
            assert account_id == packet.order.account_id
            return reference

    operations = object.__new__(probe._KISMockProbeOperations)
    operations._reference_store = SourceReferenceStore()
    operations._execution_reader = SourceExecutionReader()

    operations.run("executionRead", packet)

    assert calls == [
        (
            reference,
            packet.execution.start,
            packet.execution.end,
            packet.execution.recent,
        )
    ]


def test_balance_diagnostic_packet_rejects_full_probe_cap_before_runtime(
    secure_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_path, _ = _write_packet(secure_tmp_path)
    _rewrite_packet(packet_path, ("probeType",), "BALANCE_DIAGNOSTIC")
    packet_sha = _rewrite_packet(packet_path, ("steps",), ["balance"])
    built = False
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)

    def factory(_packet: probe.KISMockApprovalPacket) -> FakeOperations:
        nonlocal built
        built = True
        return FakeOperations()

    with pytest.raises(probe.KISMockApprovalRejected, match="contract"):
        probe.execute_approved_probe(
            packet_path,
            now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
            expected_approval_id="approval-s3-online-test",
            expected_packet_sha256=packet_sha,
            repository_root=secure_tmp_path,
            operations_factory=factory,
            approval_consumer=allow_replay_consumer,
        )

    assert built is False


def test_probe_runtime_requires_packet_account_to_match_bound_mock_account(
    secure_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_path, packet_sha = _write_packet(secure_tmp_path)
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)
    packet = probe._load_packet(
        packet_path,
        now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
        expected_approval_id="approval-s3-online-test",
        expected_packet_sha256=packet_sha,
        repository_root=secure_tmp_path,
    )

    monkeypatch.setattr(
        probe,
        "_operator_approval_value",
        lambda _name: "",
        raising=False,
    )
    with pytest.raises(probe.KISMockApprovalRejected, match="bound account"):
        probe._require_bound_account_id(packet.order.account_id)

    monkeypatch.setattr(
        probe,
        "_operator_approval_value",
        lambda _name: "acct_" + "3" * 32,
    )
    with pytest.raises(probe.KISMockApprovalRejected, match="bound account"):
        probe._require_bound_account_id(packet.order.account_id)

    monkeypatch.setattr(
        probe,
        "_operator_approval_value",
        lambda _name: packet.order.account_id,
    )
    assert probe._require_bound_account_id(packet.order.account_id) == packet.order.account_id


def test_probe_preserves_allowlisted_failure_leaf_without_raw_exception(
    secure_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.brokerage.kis_mock_online_client import (
        KISMockBrokerageError,
        KISMockFailureReason,
    )

    packet_path, packet_sha = _write_packet(secure_tmp_path)
    operations = FakeOperations(
        fail_at="balance",
        failure=KISMockBrokerageError(
            KISMockFailureReason.PROVIDER_REJECTED,
            provider_code="SAFE001",
        ),
    )
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)

    with pytest.raises(probe.KISMockProbeFailed) as captured:
        probe.execute_approved_probe(
            packet_path,
            now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
            expected_approval_id="approval-s3-online-test",
            expected_packet_sha256=packet_sha,
            repository_root=secure_tmp_path,
            operations_factory=lambda _packet: operations,
            approval_consumer=allow_replay_consumer,
        )

    assert captured.value.reason_code == "BROKERAGE_PROVIDER_REJECTED"
    assert captured.value.provider_code == "SAFE001"
    assert captured.value.http_status is None
    assert "provider" not in str(captured.value).lower()
    assert operations.calls == ["balance"]


def test_probe_replaces_untyped_exception_text_with_closed_reason(
    secure_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_path, packet_sha = _write_packet(secure_tmp_path)
    sensitive_text = "raw-account-00000000 provider-message"
    operations = FakeOperations(
        fail_at="balance",
        failure=RuntimeError(sensitive_text),
    )
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)

    with pytest.raises(probe.KISMockProbeFailed) as captured:
        probe.execute_approved_probe(
            packet_path,
            now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
            expected_approval_id="approval-s3-online-test",
            expected_packet_sha256=packet_sha,
            repository_root=secure_tmp_path,
            operations_factory=lambda _packet: operations,
            approval_consumer=allow_replay_consumer,
        )

    assert captured.value.reason_code == "UNCLASSIFIED_FAILURE"
    assert sensitive_text not in str(captured.value)
    assert sensitive_text not in repr(captured.value)


def test_cli_failure_json_contains_only_bounded_diagnostics(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_probe(*_args: object, **_kwargs: object) -> probe.ProbeSummary:
        raise probe.KISMockProbeFailed(
            "balance",
            {"tokenP": 1, "brokerage": 1},
            reason_code="BROKERAGE_PROVIDER_REJECTED",
            provider_code="SAFE001",
            http_status=None,
        )

    monkeypatch.setattr(probe, "execute_approved_probe", fail_probe)
    monkeypatch.setattr(
        probe,
        "_operator_approval_value",
        lambda name: {
            "S3_KIS_MOCK_EXACT_APPROVAL_ID": "approval-s3-online-test",
            "S3_KIS_MOCK_EXACT_APPROVAL_SHA256": "a" * 64,
        }[name],
        raising=False,
    )

    assert probe.main(["--approval-packet", "/tmp/unused"]) == 1
    output = json.loads(capsys.readouterr().err)
    assert output == {
        "artifactWrites": 0,
        "failedStep": "balance",
        "physicalReservations": {"brokerage": 1, "tokenP": 1},
        "providerCode": "SAFE001",
        "reasonCode": "BROKERAGE_PROVIDER_REJECTED",
        "retryCount": 0,
        "status": "FAILED",
    }


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("kisLiveOrderEnabled",), 0),
        (("retryCount",), False),
        (("evidence", "securityFindings"), False),
        (("order", "quantity"), True),
        (("execution", "recent"), 1),
    ],
)
def test_packet_rejects_boolean_integer_aliases(
    secure_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: tuple[str, ...],
    value: object,
) -> None:
    packet_path, _ = _write_packet(secure_tmp_path)
    packet_sha = _rewrite_packet(packet_path, path, value)
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)

    with pytest.raises(probe.KISMockApprovalRejected, match="contract"):
        probe.execute_approved_probe(
            packet_path,
            now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
            expected_approval_id="approval-s3-online-test",
            expected_packet_sha256=packet_sha,
            repository_root=secure_tmp_path,
            operations_factory=lambda _packet: FakeOperations(),
            approval_consumer=allow_replay_consumer,
        )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (
            ("evidence", "requiredChecks"),
            [
                {"name": "Contract schema validation", "conclusion": "SUCCESS"},
                {"name": "Kotlin ktlint and build", "conclusion": "SUCCESS"},
                {"name": "Python quality gates", "conclusion": "SUCCESS"},
                {"name": "Repo hygiene", "conclusion": "SUCCESS"},
            ],
        ),
        (("executionCommand",), "printf unsafe"),
    ],
)
def test_packet_rejects_missing_required_ci_or_changed_execution_command(
    secure_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: tuple[str, ...],
    value: object,
) -> None:
    packet_path, _ = _write_packet(secure_tmp_path)
    packet_sha = _rewrite_packet(packet_path, path, value)
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)

    with pytest.raises(probe.KISMockApprovalRejected):
        probe.execute_approved_probe(
            packet_path,
            now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
            expected_approval_id="approval-s3-online-test",
            expected_packet_sha256=packet_sha,
            repository_root=secure_tmp_path,
            operations_factory=lambda _packet: FakeOperations(),
            approval_consumer=allow_replay_consumer,
        )


def _write_packet(tmp_path: Path) -> tuple[Path, str]:
    report_path = tmp_path / "security-report.md"
    report_path.write_text("SECURITY_SCAN_COMPLETE\n", encoding="utf-8")
    report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
    packet_path = tmp_path / "approval.json"
    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "approvalId": "approval-s3-online-test",
        "issuedAt": "2030-01-02T03:00:00Z",
        "expiresAt": "2030-01-02T04:00:00Z",
        "mode": "KIS_MOCK",
        "kisLiveOrderEnabled": False,
        "retryCount": 0,
        "artifactWrites": 0,
        "providerCallsBeforeApproval": 0,
        "repository": {
            "root": str(tmp_path),
            "branchRef": "feature/s3-3-fill-events-reconciliation",
            "headSha": "a" * 40,
            "remoteHeadSha": "a" * 40,
            "pullRequest": 55,
        },
        "evidence": {
            "ciHeadSha": "a" * 40,
            "requiredChecks": [
                {"name": "Contract schema validation", "conclusion": "SUCCESS"},
                {"name": "Spring OpenAPI drift", "conclusion": "SUCCESS"},
                {"name": "Kotlin ktlint and build", "conclusion": "SUCCESS"},
                {"name": "Python quality gates", "conclusion": "SUCCESS"},
                {"name": "Repo hygiene", "conclusion": "SUCCESS"},
            ],
            "securityHeadSha": "a" * 40,
            "securityStatus": "SECURITY_SCAN_COMPLETE",
            "securityFindings": 0,
            "securityReportPath": str(report_path),
            "securityReportSha256": report_sha,
        },
        "physicalCaps": {"tokenP": 1, "brokerage": 5},
        "redisBaseline": {
            "restPttlMillis": -2,
            "tokenPttlMillis": -2,
            "observedAt": "2030-01-02T03:00:00Z",
        },
        "referenceTtlSeconds": 900,
        "order": {
            "orderId": "ord_mock_" + "1" * 32,
            "accountId": "acct_" + "2" * 32,
            "symbol": "005930",
            "side": "BUY",
            "orderType": "LIMIT",
            "quantity": 1,
            "limitPriceKrw": 70_000,
        },
        "execution": {
            "from": "2030-01-02",
            "to": "2030-01-02",
            "recent": True,
        },
        "steps": [
            "balance",
            "buyable",
            "submitLimitBuy",
            "cancelFull",
            "executionRead",
        ],
        "stopRule": "FIRST_FAILURE_STOPS_REMAINING_CALLS",
        "executionCommand": (
            f"uv run --directory {tmp_path}/workspaces/decision-platform/python-services "
            "--frozen kis-mock-brokerage-probe "
            f"--approval-packet {packet_path}"
        ),
    }
    packet_sha = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    payload["packetSha256"] = packet_sha
    packet_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    packet_path.chmod(0o600)
    return packet_path, packet_sha


def _rewrite_packet(
    packet_path: Path,
    field_path: tuple[str, ...],
    value: object,
) -> str:
    payload = json.loads(packet_path.read_text(encoding="utf-8"))
    target = payload
    for field in field_path[:-1]:
        target = target[field]
    target[field_path[-1]] = value
    payload.pop("packetSha256")
    packet_sha = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    payload["packetSha256"] = packet_sha
    packet_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    packet_path.chmod(0o600)
    return packet_sha


def _run_git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "core.fsmonitor=false", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
