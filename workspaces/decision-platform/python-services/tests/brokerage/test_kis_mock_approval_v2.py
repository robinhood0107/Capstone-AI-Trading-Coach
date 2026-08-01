from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.brokerage import kis_mock_approval_author as author
from app.brokerage import kis_mock_approval_probe as probe


class _FakeOperations:
    """v2 packet contract test는 provider transport 대신 호출 표면만 기록한다."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.closed = False

    def run(self, operation: str, _packet: probe.ApprovalPacket) -> None:
        self.calls.append(operation)

    def counts(self) -> dict[str, int]:
        return {"tokenP": 0, "brokerage": len(self.calls)}

    def close(self) -> None:
        self.closed = True


def _allow_replay(_packet: probe.ApprovalPacket, _now: datetime) -> None:
    return None


@pytest.fixture
def secure_directory(tmp_path: Path) -> Path:
    root = Path(tempfile.mkdtemp(prefix="s3-online-v2-test-", dir="/tmp"))
    directory = root / "operator-packets"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    try:
        yield directory
    finally:
        shutil.rmtree(root)


def test_v2_accepts_dynamic_pr_and_binds_all_final_head_evidence(
    secure_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_path, packet_sha = _write_v2_packet(secure_directory, pull_request=77)
    operations = _FakeOperations()
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)
    monkeypatch.setattr(probe, "_require_clean_repository", lambda _root: None)

    summary = probe.execute_approved_probe(
        packet_path,
        now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
        expected_approval_id="approval-s3-online-v2-test",
        expected_packet_sha256=packet_sha,
        repository_root=secure_directory,
        operations_factory=lambda _packet: operations,
        approval_consumer=_allow_replay,
    )

    assert operations.calls == [
        "balance",
        "buyable",
        "submitLimitBuy",
        "cancelFull",
        "executionRead",
    ]
    assert operations.closed is True
    assert summary.physical_reservations == {"tokenP": 0, "brokerage": 5}


def test_v1_history_stays_hard_locked_when_v2_allows_dynamic_pr(
    secure_directory: Path,
) -> None:
    v1 = _packet_document(secure_directory, schema_version=1, pull_request=55)
    v1["packetSha256"] = _packet_digest(v1)
    assert isinstance(probe.parse_approval_packet(v1), probe.KISMockApprovalPacket)

    v1["repository"]["pullRequest"] = 77
    v1["packetSha256"] = _packet_digest(v1)
    with pytest.raises(probe.KISMockApprovalRejected, match="contract"):
        probe.parse_approval_packet(v1)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("nonce",), "not-256-bits"),
        (("repository", "baseRef"), "release"),
        (("repository", "remoteHeadSha"), "b" * 40),
        (("evidence", "ciHeadSha"), "b" * 40),
        (("evidence", "securityHeadSha"), "b" * 40),
    ],
)
def test_v2_rejects_nonce_or_final_head_evidence_drift_before_runtime(
    secure_directory: Path,
    path: tuple[str, ...],
    value: object,
) -> None:
    document = _packet_document(secure_directory, schema_version=2, pull_request=77)
    target: dict[str, Any] = document
    for field in path[:-1]:
        target = target[field]
    target[path[-1]] = value
    document["packetSha256"] = _packet_digest(document)

    with pytest.raises(probe.KISMockApprovalRejected, match="contract"):
        probe.parse_approval_packet(document)


def test_cancel_recovery_only_exposes_cancel_and_execution_read(
    secure_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_path, packet_sha = _write_v2_packet(
        secure_directory,
        pull_request=77,
        profile="CANCEL_RECOVERY",
        recovery_failed_step="cancelFull",
    )
    operations = _FakeOperations()
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)
    monkeypatch.setattr(probe, "_require_clean_repository", lambda _root: None)

    summary = probe.execute_approved_probe(
        packet_path,
        now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
        expected_approval_id="approval-s3-online-v2-test",
        expected_packet_sha256=packet_sha,
        repository_root=secure_directory,
        operations_factory=lambda _packet: operations,
        approval_consumer=_allow_replay,
    )

    assert operations.calls == ["cancelFull", "executionRead"]
    assert "submitLimitBuy" not in operations.calls
    assert summary.physical_reservations == {"tokenP": 0, "brokerage": 2}


def test_cancel_recovery_rejects_new_order_surface_before_redis_or_runtime(
    secure_directory: Path,
) -> None:
    document = _packet_document(
        secure_directory,
        schema_version=2,
        pull_request=77,
        profile="CANCEL_RECOVERY",
        recovery_failed_step="cancelFull",
    )
    document["steps"] = ["cancelFull", "submitLimitBuy", "executionRead"]
    document["packetSha256"] = _packet_digest(document)

    with pytest.raises(probe.KISMockApprovalRejected, match="contract"):
        probe.parse_approval_packet(document)


def test_v2_reader_rejects_a_symlinked_packet_parent_before_runtime(
    secure_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_path, packet_sha = _write_v2_packet(secure_directory, pull_request=77)
    linked_parent = secure_directory.parent / "linked-operator-packets"
    linked_parent.symlink_to(secure_directory, target_is_directory=True)
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)
    monkeypatch.setattr(probe, "_require_clean_repository", lambda _root: None)

    with pytest.raises(probe.KISMockApprovalRejected, match="file boundary"):
        probe.execute_approved_probe(
            linked_parent / packet_path.name,
            now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
            expected_approval_id="approval-s3-online-v2-test",
            expected_packet_sha256=packet_sha,
            repository_root=secure_directory,
            operations_factory=lambda _packet: _FakeOperations(),
            approval_consumer=_allow_replay,
        )


def test_v2_source_anchor_is_deterministically_bound_to_source_packet_and_nonce() -> None:
    anchor = probe.approval_anchor_for_source("c" * 64, "d" * 64)

    assert anchor == hashlib.sha256((("c" * 64) + "\0" + ("d" * 64)).encode()).hexdigest()
    assert anchor != probe.approval_anchor_for_source("c" * 64, "e" * 64)


def test_author_writer_creates_only_a_new_owner_private_regular_file(
    secure_directory: Path,
) -> None:
    packet_path = secure_directory / "approval.json"
    payload = b'{"packetSha256":"synthetic"}\n'

    author.write_new_approval_packet(packet_path, payload)

    metadata = packet_path.stat()
    assert stat.S_ISREG(metadata.st_mode)
    assert stat.S_IMODE(metadata.st_mode) == 0o600
    assert metadata.st_uid == os.getuid()
    assert metadata.st_nlink == 1
    assert packet_path.read_bytes() == payload


def test_author_collects_dynamic_pr_branch_head_and_required_checks(
    secure_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    required_checks = [
        {"name": name, "conclusion": "SUCCESS"} for name in sorted(probe._REQUIRED_CI_CHECKS)
    ]
    monkeypatch.setattr(author, "_require_clean_repository", lambda _root: None)
    monkeypatch.setattr(author, "_git_revision", lambda _root, _ref: "a" * 40)

    def fake_run(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if arguments[:2] == ["git", "symbolic-ref"]:
            return subprocess.CompletedProcess(arguments, 0, "feature/test-approved-packet\n", "")
        assert arguments[:3] == ["gh", "pr", "view"]
        return subprocess.CompletedProcess(
            arguments,
            0,
            json.dumps(
                {
                    "number": 77,
                    "headRefName": "feature/test-approved-packet",
                    "baseRefName": "main",
                    "headRefOid": "a" * 40,
                    "statusCheckRollup": required_checks,
                }
            ),
            "",
        )

    monkeypatch.setattr(author.subprocess, "run", fake_run)

    branch, head, checks = author._collect_current_pr_evidence(secure_directory, pull_request=77)

    assert branch == "feature/test-approved-packet"
    assert head == "a" * 40
    assert checks == required_checks


def test_author_rejects_dynamic_pr_when_github_head_is_not_local_head(
    secure_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(author, "_require_clean_repository", lambda _root: None)
    monkeypatch.setattr(author, "_git_revision", lambda _root, _ref: "a" * 40)

    def fake_run(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if arguments[:2] == ["git", "symbolic-ref"]:
            return subprocess.CompletedProcess(arguments, 0, "feature/test-approved-packet\n", "")
        return subprocess.CompletedProcess(
            arguments,
            0,
            json.dumps(
                {
                    "number": 77,
                    "headRefName": "feature/test-approved-packet",
                    "baseRefName": "main",
                    "headRefOid": "b" * 40,
                    "statusCheckRollup": [],
                }
            ),
            "",
        )

    monkeypatch.setattr(author.subprocess, "run", fake_run)

    with pytest.raises(author.KISMockApprovalAuthorRejected, match="one final HEAD"):
        author._collect_current_pr_evidence(secure_directory, pull_request=77)


@pytest.mark.parametrize("entry_kind", ["symlink", "directory", "hardlink"])
def test_author_writer_rejects_hostile_or_existing_output_without_overwrite(
    secure_directory: Path,
    entry_kind: str,
) -> None:
    packet_path = secure_directory / "approval.json"
    outside = secure_directory.parent / "outside-sentinel"
    outside.write_bytes(b"outside remains unchanged")
    if entry_kind == "symlink":
        packet_path.symlink_to(outside)
    elif entry_kind == "directory":
        packet_path.mkdir()
    else:
        os.link(outside, packet_path)

    with pytest.raises(author.KISMockApprovalAuthorRejected):
        author.write_new_approval_packet(packet_path, b"new packet")

    assert outside.read_bytes() == b"outside remains unchanged"


def _write_v2_packet(
    directory: Path,
    *,
    pull_request: int,
    profile: str = "FULL",
    recovery_failed_step: str | None = None,
) -> tuple[Path, str]:
    document = _packet_document(
        directory,
        schema_version=2,
        pull_request=pull_request,
        profile=profile,
        recovery_failed_step=recovery_failed_step,
    )
    digest = _packet_digest(document)
    document["packetSha256"] = digest
    packet_path = directory / f"{profile.lower()}-approval.json"
    packet_path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    packet_path.chmod(0o600)
    return packet_path, digest


def _packet_document(
    directory: Path,
    *,
    schema_version: int,
    pull_request: int,
    profile: str = "FULL",
    recovery_failed_step: str | None = None,
) -> dict[str, Any]:
    report_path = directory / "security-report.md"
    report_path.write_text("SECURITY_SCAN_COMPLETE\n", encoding="utf-8")
    report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
    document: dict[str, Any] = {
        "schemaVersion": schema_version,
        "approvalId": "approval-s3-online-v2-test",
        "issuedAt": "2030-01-02T03:00:00Z",
        "expiresAt": "2030-01-02T04:00:00Z",
        "mode": "KIS_MOCK",
        "kisLiveOrderEnabled": False,
        "retryCount": 0,
        "artifactWrites": 0,
        "providerCallsBeforeApproval": 0,
        "probeType": profile,
        "repository": {
            "root": str(directory),
            "branchRef": "feature/integrated-news-rag-cross-market-s5",
            "headSha": "a" * 40,
            "remoteHeadSha": "a" * 40,
            "pullRequest": pull_request,
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
            "limitPriceKrw": 70000,
        },
        "execution": {"from": "2030-01-02", "to": "2030-01-02", "recent": True},
        "steps": ["balance", "buyable", "submitLimitBuy", "cancelFull", "executionRead"],
        "stopRule": "FIRST_FAILURE_STOPS_REMAINING_CALLS",
        "executionCommand": (
            f"uv run --directory {directory}/workspaces/decision-platform/python-services "
            "--frozen kis-mock-brokerage-probe "
            f"--approval-packet {directory / f'{profile.lower()}-approval.json'}"
        ),
    }
    if schema_version == 2:
        document["nonce"] = "d" * 64
        document["repository"]["baseRef"] = "main"
    if profile == "CANCEL_RECOVERY":
        document["steps"] = ["cancelFull", "executionRead"]
        document["physicalCaps"]["brokerage"] = 2
        document["recoveryOf"] = {
            "sourceApprovalId": "approval-s3-online-source",
            "sourcePacketSha256": "c" * 64,
            "sourceNonce": "e" * 64,
            "failedStep": recovery_failed_step,
        }
    return document


def _packet_digest(document: dict[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("packetSha256", None)
    return hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
