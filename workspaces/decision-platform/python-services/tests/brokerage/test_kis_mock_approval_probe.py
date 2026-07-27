from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.brokerage import kis_mock_approval_probe as probe


@pytest.fixture
def secure_tmp_path() -> Path:
    path = Path(tempfile.mkdtemp(prefix="s3-online-probe-test-", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path)


class FakeOperations:
    def __init__(self, *, fail_at: str | None = None) -> None:
        self.fail_at = fail_at
        self.calls: list[str] = []
        self.closed = False

    def run(self, operation: str, packet: probe.KISMockApprovalPacket) -> None:
        self.calls.append(operation)
        if operation == self.fail_at:
            raise RuntimeError("synthetic")

    def counts(self) -> dict[str, int]:
        return {"tokenP": 0, "brokerage": len(self.calls)}

    def close(self) -> None:
        self.closed = True


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
        )

    assert captured.value.failed_step == "submitLimitBuy"
    assert operations.calls == ["balance", "buyable", "submitLimitBuy"]
    assert operations.closed is True


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
