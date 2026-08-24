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


@pytest.fixture(autouse=True)
def bypass_live_pr_evidence(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """일반 v2 packet test는 GitHub 상태 대신 별도 live-evidence 회귀 test만 검증한다."""

    if "live_pr_evidence" not in request.fixturenames:
        monkeypatch.setattr(
            probe,
            "_require_current_v2_pr_evidence",
            lambda _packet, _repository_root: None,
            raising=False,
        )
    monkeypatch.setattr(
        probe,
        "_require_recovery_source_outcome",
        lambda _packet: None,
        raising=False,
    )


@pytest.fixture
def live_pr_evidence() -> None:
    """live GitHub response parser를 직접 검증하는 test만 autouse bypass를 해제한다."""


class _FakeOperations:
    """v2 packet contract test는 provider transport 대신 호출 표면만 기록한다."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.closed = False
        self.outcomes: list[str | None] = []

    def run(self, operation: str, _packet: probe.ApprovalPacket) -> None:
        self.calls.append(operation)

    def counts(self) -> dict[str, int]:
        return {"tokenP": 0, "brokerage": len(self.calls)}

    def close(self) -> None:
        self.closed = True

    def record_outcome(
        self,
        _packet: probe.ApprovalPacket,
        failed_step: str | None,
    ) -> None:
        self.outcomes.append(failed_step)


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


def test_v2_accepts_merged_main_execution_head_without_weakening_legacy_open_pr_mode(
    secure_directory: Path,
) -> None:
    """Pre-S5 execution packet은 merged main SHA를 쓰되 기존 v2 OPEN_PR bytes도 계속 수용한다."""

    merged = _packet_document(secure_directory, schema_version=2, pull_request=104)
    merged["repository"].update(
        {
            "branchRef": "main",
            "evidenceMode": "MERGED_MAIN",
        }
    )
    merged["packetSha256"] = _packet_digest(merged)
    parsed_merged = probe.parse_approval_packet(merged)
    assert isinstance(parsed_merged, probe.KISMockApprovalPacketV2)
    assert parsed_merged.repository.evidence_mode == "MERGED_MAIN"

    legacy_open_pr = _packet_document(secure_directory, schema_version=2, pull_request=77)
    legacy_open_pr["packetSha256"] = _packet_digest(legacy_open_pr)
    parsed_legacy = probe.parse_approval_packet(legacy_open_pr)
    assert isinstance(parsed_legacy, probe.KISMockApprovalPacketV2)
    assert parsed_legacy.repository.evidence_mode == "OPEN_PR"


def test_v3_full_packet_is_exactly_seven_reconciliation_steps_and_seven_calls(
    secure_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _packet_document(secure_directory, schema_version=3, pull_request=104)
    document["steps"] = [
        "preBalance",
        "buyable",
        "submitLimitBuy",
        "cancelFull",
        "executionRead",
        "postBalance",
        "openOrderReconciliation",
    ]
    document["physicalCaps"]["brokerage"] = 7
    document["packetSha256"] = _packet_digest(document)

    packet = probe.parse_approval_packet(document)

    assert isinstance(packet, probe.KISMockApprovalPacketV3)
    assert packet.schema_version == 3
    assert packet.steps == tuple(document["steps"])
    assert packet.physical_caps.token_p == 1
    assert packet.physical_caps.brokerage == 7
    assert packet.retry_count == 0

    captured: dict[str, object] = {}
    approval = object()
    monkeypatch.setattr(
        probe,
        "load_and_verify_execution_approval",
        lambda *args, **kwargs: captured.update(kwargs) or approval,
    )
    monkeypatch.setattr(
        probe,
        "claim_signed_provider_approval",
        lambda value: captured.update(claim=value),
    )
    probe._consume_exact_approval_once(packet, datetime(2030, 1, 2, 3, 10, tzinfo=UTC))

    assert captured["payload_sha256"] == packet.packet_sha256
    assert captured["exact_operations"] == packet.steps
    assert captured["physical_call_cap"] == 8
    assert captured["claim"] is approval


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("steps", ["preBalance", "buyable"]),
        ("physicalCaps", {"tokenP": 1, "brokerage": 6}),
        ("probeType", "CANCEL_RECOVERY"),
    ],
)
def test_v3_rejects_any_weakened_final_reconciliation_contract(
    secure_directory: Path,
    field: str,
    value: object,
) -> None:
    document = _packet_document(secure_directory, schema_version=3, pull_request=104)
    document.update(
        {
            "steps": [
                "preBalance",
                "buyable",
                "submitLimitBuy",
                "cancelFull",
                "executionRead",
                "postBalance",
                "openOrderReconciliation",
            ],
            "physicalCaps": {"tokenP": 1, "brokerage": 7},
        }
    )
    document[field] = value
    document["packetSha256"] = _packet_digest(document)

    with pytest.raises(probe.KISMockApprovalRejected, match="contract"):
        probe.parse_approval_packet(document)


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


def test_v2_late_failed_ci_blocks_before_single_use_claim_or_runtime(
    secure_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """author 시점 이후 rerun된 CI가 실패하면 packet TTL 안이어도 provider 표면을 열지 않는다."""

    packet_path, packet_sha = _write_v2_packet(secure_directory, pull_request=77)
    operations = _FakeOperations()
    consumed = False
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)
    monkeypatch.setattr(probe, "_require_clean_repository", lambda _root: None)

    def reject_late_ci(
        _packet: probe.KISMockApprovalPacketV2,
        _repository_root: Path,
    ) -> None:
        raise probe.KISMockApprovalRejected("PR required checks are no longer successful")

    def consume(_packet: probe.ApprovalPacket, _now: datetime) -> None:
        nonlocal consumed
        consumed = True

    monkeypatch.setattr(probe, "_require_current_v2_pr_evidence", reject_late_ci)

    with pytest.raises(probe.KISMockApprovalRejected, match="no longer successful"):
        probe.execute_approved_probe(
            packet_path,
            now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
            expected_approval_id="approval-s3-online-v2-test",
            expected_packet_sha256=packet_sha,
            repository_root=secure_directory,
            operations_factory=lambda _packet: operations,
            approval_consumer=consume,
        )

    assert consumed is False
    assert operations.calls == []


@pytest.mark.parametrize(
    ("state", "is_draft", "failed_check"),
    [("CLOSED", False, None), ("OPEN", True, None), ("OPEN", False, "Python quality gates")],
)
def test_v2_live_pr_revalidation_requires_active_green_head(
    secure_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
    live_pr_evidence: None,
    state: str,
    is_draft: bool,
    failed_check: str | None,
) -> None:
    """runtime recheck은 author 때의 historical statusCheckRollup을 재사용하지 않는다."""

    packet_path, _ = _write_v2_packet(secure_directory, pull_request=77)
    raw_packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet = probe.parse_approval_packet(raw_packet)
    assert isinstance(packet, probe.KISMockApprovalPacketV2)
    checks = [
        {
            "name": name,
            "conclusion": "FAILURE" if name == failed_check else "SUCCESS",
        }
        for name in sorted(probe._REQUIRED_CI_CHECKS)
    ]

    def fake_run(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert arguments[:3] == ["gh", "pr", "view"]
        return subprocess.CompletedProcess(
            arguments,
            0,
            json.dumps(
                {
                    "number": 77,
                    "state": state,
                    "isDraft": is_draft,
                    "headRefName": "feature/integrated-news-rag-cross-market-s5",
                    "baseRefName": "main",
                    "headRefOid": "a" * 40,
                    "statusCheckRollup": checks,
                }
            ),
            "",
        )

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    with pytest.raises(probe.KISMockApprovalRejected):
        probe._require_current_v2_pr_evidence(packet, secure_directory)


def test_v2_merged_main_revalidation_requires_merge_sha_and_post_merge_checks(
    secure_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
    live_pr_evidence: None,
) -> None:
    """packet claim 직전에도 merge SHA의 check-runs를 다시 읽어 PR head CI 재사용을 막는다."""

    raw_packet = _packet_document(secure_directory, schema_version=2, pull_request=104)
    raw_packet["repository"].update(
        {
            "branchRef": "main",
            "evidenceMode": "MERGED_MAIN",
        }
    )
    raw_packet["packetSha256"] = _packet_digest(raw_packet)
    packet = probe.parse_approval_packet(raw_packet)
    assert isinstance(packet, probe.KISMockApprovalPacketV2)
    observed: list[tuple[str, ...]] = []

    def fake_run(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.append(tuple(arguments))
        if arguments[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(
                arguments,
                0,
                json.dumps(
                    {
                        "number": 104,
                        "state": "MERGED",
                        "isDraft": False,
                        "baseRefName": "main",
                        "mergeCommit": {"oid": "a" * 40},
                    }
                ),
                "",
            )
        if arguments[:2] == ["gh", "api"] and arguments[4].endswith("/git/ref/heads/main"):
            return subprocess.CompletedProcess(
                arguments,
                0,
                json.dumps({"object": {"sha": "a" * 40}}),
                "",
            )
        assert arguments[:2] == ["gh", "api"]
        return subprocess.CompletedProcess(
            arguments,
            0,
            json.dumps(
                {
                    "check_runs": [
                        {
                            "name": name,
                            "conclusion": "success",
                            "status": "completed",
                            "head_sha": "a" * 40,
                            "app": {"slug": "github-actions"},
                        }
                        for name in sorted(probe._REQUIRED_CI_CHECKS)
                    ]
                }
            ),
            "",
        )

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    probe._require_current_v2_pr_evidence(packet, secure_directory)

    assert any(command[:3] == ("gh", "pr", "view") for command in observed)
    assert any(command[:2] == ("gh", "api") for command in observed)


def test_v2_merged_main_revalidation_rejects_green_checks_from_another_sha(
    secure_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
    live_pr_evidence: None,
) -> None:
    """required check 이름이 같아도 head_sha가 EXECUTION_HEAD와 다르면 outbound 전에 거부한다."""

    raw_packet = _packet_document(secure_directory, schema_version=2, pull_request=104)
    raw_packet["repository"].update(
        {
            "branchRef": "main",
            "evidenceMode": "MERGED_MAIN",
        }
    )
    raw_packet["packetSha256"] = _packet_digest(raw_packet)
    packet = probe.parse_approval_packet(raw_packet)
    assert isinstance(packet, probe.KISMockApprovalPacketV2)

    def fake_run(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if arguments[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(
                arguments,
                0,
                json.dumps(
                    {
                        "number": 104,
                        "state": "MERGED",
                        "isDraft": False,
                        "baseRefName": "main",
                        "mergeCommit": {"oid": "a" * 40},
                    }
                ),
                "",
            )
        if arguments[:2] == ["gh", "api"] and arguments[4].endswith("/git/ref/heads/main"):
            return subprocess.CompletedProcess(
                arguments,
                0,
                json.dumps({"object": {"sha": "a" * 40}}),
                "",
            )
        return subprocess.CompletedProcess(
            arguments,
            0,
            json.dumps(
                {
                    "check_runs": [
                        {
                            "name": name,
                            "conclusion": "success",
                            "status": "completed",
                            "head_sha": "b" * 40,
                            "app": {"slug": "github-actions"},
                        }
                        for name in sorted(probe._REQUIRED_CI_CHECKS)
                    ]
                }
            ),
            "",
        )

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    with pytest.raises(probe.KISMockApprovalRejected, match="post-merge checks"):
        probe._require_current_v2_pr_evidence(packet, secure_directory)


@pytest.mark.parametrize(
    ("remote_main_sha", "check_app", "expected_message"),
    [
        ("b" * 40, "github-actions", "remote main"),
        ("a" * 40, "untrusted-check-app", "post-merge checks"),
    ],
)
def test_v2_merged_main_revalidation_rejects_remote_drift_and_spoofed_check_app(
    secure_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
    live_pr_evidence: None,
    remote_main_sha: str,
    check_app: str,
    expected_message: str,
) -> None:
    """stale origin/main과 제3자 same-name check는 exact execution authority가 아니다."""

    raw_packet = _packet_document(secure_directory, schema_version=2, pull_request=104)
    raw_packet["repository"].update(
        {
            "branchRef": "main",
            "evidenceMode": "MERGED_MAIN",
        }
    )
    raw_packet["packetSha256"] = _packet_digest(raw_packet)
    packet = probe.parse_approval_packet(raw_packet)
    assert isinstance(packet, probe.KISMockApprovalPacketV2)

    def fake_run(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if arguments[:3] == ["gh", "pr", "view"]:
            payload: object = {
                "number": 104,
                "state": "MERGED",
                "isDraft": False,
                "baseRefName": "main",
                "mergeCommit": {"oid": "a" * 40},
            }
        elif arguments[:2] == ["gh", "api"] and arguments[4].endswith(
            "/git/ref/heads/main"
        ):
            payload = {"object": {"sha": remote_main_sha}}
        else:
            payload = {
                "check_runs": [
                    {
                        "name": name,
                        "conclusion": "success",
                        "status": "completed",
                        "head_sha": "a" * 40,
                        "app": {"slug": check_app},
                    }
                    for name in sorted(probe._REQUIRED_CI_CHECKS)
                ]
            }
        return subprocess.CompletedProcess(arguments, 0, json.dumps(payload), "")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    with pytest.raises(probe.KISMockApprovalRejected, match=expected_message):
        probe._require_current_v2_pr_evidence(packet, secure_directory)


def test_v2_recovery_rejects_forged_cancel_step_before_claim_or_runtime(
    secure_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """executionRead 실제 실패를 cancelFull로 바꾼 recovery는 cancel provider call 이전에 닫는다."""

    packet_path, packet_sha = _write_v2_packet(
        secure_directory,
        pull_request=77,
        profile="CANCEL_RECOVERY",
        recovery_failed_step="cancelFull",
    )
    operations = _FakeOperations()
    consumed = False
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)
    monkeypatch.setattr(probe, "_require_clean_repository", lambda _root: None)

    def reject_forged_recovery(_packet: probe.KISMockApprovalPacketV2) -> None:
        raise probe.KISMockApprovalRejected("recovery source outcome does not match")

    def consume(_packet: probe.ApprovalPacket, _now: datetime) -> None:
        nonlocal consumed
        consumed = True

    monkeypatch.setattr(probe, "_require_recovery_source_outcome", reject_forged_recovery)

    with pytest.raises(probe.KISMockApprovalRejected, match="source outcome"):
        probe.execute_approved_probe(
            packet_path,
            now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
            expected_approval_id="approval-s3-online-v2-test",
            expected_packet_sha256=packet_sha,
            repository_root=secure_directory,
            operations_factory=lambda _packet: operations,
            approval_consumer=consume,
        )

    assert consumed is False
    assert operations.calls == []


def test_v2_deadline_blocks_later_step_before_its_provider_reservation(
    secure_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """limiter 대기 등으로 TTL을 넘기면 다음 operation을 dispatch하지 않는다."""

    packet_path, packet_sha = _write_v2_packet(secure_directory, pull_request=77)
    operations = _FakeOperations()
    timestamps = iter(
        (
            datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
            datetime(2030, 1, 2, 4, 0, tzinfo=UTC),
        )
    )
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)
    monkeypatch.setattr(probe, "_require_clean_repository", lambda _root: None)

    with pytest.raises(probe.KISMockApprovalRejected, match="not inside its TTL"):
        probe.execute_approved_probe(
            packet_path,
            now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
            expected_approval_id="approval-s3-online-v2-test",
            expected_packet_sha256=packet_sha,
            repository_root=secure_directory,
            operations_factory=lambda _packet: operations,
            approval_consumer=_allow_replay,
            clock=lambda: next(timestamps),
        )

    assert operations.calls == ["balance"]
    assert operations.counts() == {"tokenP": 0, "brokerage": 1}


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


def test_v2_rejects_a_sealed_scan_with_incomplete_coverage_before_runtime(
    secure_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_path, _ = _write_v2_packet(secure_directory, pull_request=77)
    document = json.loads(packet_path.read_text(encoding="utf-8"))
    coverage_path = Path(document["evidence"]["securityCoveragePath"])
    coverage_path.write_text(
        json.dumps({"completeness": "partial", "scanId": "scan-v2-test"}, sort_keys=True),
        encoding="utf-8",
    )
    coverage_sha = hashlib.sha256(coverage_path.read_bytes()).hexdigest()
    document["evidence"]["securityCoverageSha256"] = coverage_sha
    manifest_path = Path(document["evidence"]["securityManifestPath"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["scan"]["artifacts"][0]["sha256"] = coverage_sha
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    document["evidence"]["securityManifestSha256"] = hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    document["packetSha256"] = _packet_digest(document)
    packet_path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    packet_path.chmod(0o600)
    monkeypatch.setattr(probe, "_git_revision", lambda _root, _ref: "a" * 40)
    monkeypatch.setattr(probe, "_require_clean_repository", lambda _root: None)

    with pytest.raises(probe.KISMockApprovalRejected, match="coverage or findings"):
        probe.execute_approved_probe(
            packet_path,
            now=datetime(2030, 1, 2, 3, 10, tzinfo=UTC),
            expected_approval_id="approval-s3-online-v2-test",
            expected_packet_sha256=document["packetSha256"],
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
                    "state": "OPEN",
                    "isDraft": False,
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
                    "state": "OPEN",
                    "isDraft": False,
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


@pytest.mark.parametrize(
    ("state", "is_draft"),
    [("CLOSED", False), ("MERGED", False), ("OPEN", True)],
)
def test_author_rejects_non_open_or_draft_pull_request(
    secure_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    is_draft: bool,
) -> None:
    """pre-merge only approval은 closed/merged/draft PR의 stale head를 쓸 수 없다."""

    required_checks = [
        {"name": name, "conclusion": "SUCCESS"} for name in sorted(probe._REQUIRED_CI_CHECKS)
    ]
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
                    "state": state,
                    "isDraft": is_draft,
                    "headRefName": "feature/test-approved-packet",
                    "baseRefName": "main",
                    "headRefOid": "a" * 40,
                    "statusCheckRollup": required_checks,
                }
            ),
            "",
        )

    monkeypatch.setattr(author.subprocess, "run", fake_run)

    with pytest.raises(author.KISMockApprovalAuthorRejected, match="not active"):
        author._collect_current_pr_evidence(secure_directory, pull_request=77)


def test_author_collects_merged_main_head_and_post_merge_checks(
    secure_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """author는 clean local main, origin/main, merged PR SHA와 post-merge check-runs를 한 SHA로 묶는다."""

    monkeypatch.setattr(author, "_require_clean_repository", lambda _root: None)
    monkeypatch.setattr(author, "_git_revision", lambda _root, _ref: "a" * 40)

    def fake_run(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if arguments[:2] == ["git", "symbolic-ref"]:
            return subprocess.CompletedProcess(arguments, 0, "main\n", "")
        if arguments[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(
                arguments,
                0,
                json.dumps(
                    {
                        "number": 104,
                        "state": "MERGED",
                        "isDraft": False,
                        "baseRefName": "main",
                        "mergeCommit": {"oid": "a" * 40},
                    }
                ),
                "",
            )
        if arguments[:2] == ["gh", "api"] and arguments[4].endswith("/git/ref/heads/main"):
            return subprocess.CompletedProcess(
                arguments,
                0,
                json.dumps({"object": {"sha": "a" * 40}}),
                "",
            )
        assert arguments[:2] == ["gh", "api"]
        return subprocess.CompletedProcess(
            arguments,
            0,
            json.dumps(
                {
                    "check_runs": [
                        {
                            "name": name,
                            "conclusion": "success",
                            "status": "completed",
                            "head_sha": "a" * 40,
                            "app": {"slug": "github-actions"},
                        }
                        for name in sorted(probe._REQUIRED_CI_CHECKS)
                    ]
                }
            ),
            "",
        )

    monkeypatch.setattr(author.subprocess, "run", fake_run)

    branch, head, checks = author._collect_merged_main_evidence(
        secure_directory,
        pull_request=104,
    )

    assert branch == "main"
    assert head == "a" * 40
    assert checks == [
        {"name": name, "conclusion": "SUCCESS"}
        for name in sorted(probe._REQUIRED_CI_CHECKS)
    ]


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
    security_evidence = _write_security_scan_evidence(directory, "a" * 40)
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
            "securityReportPath": str(security_evidence["reportPath"]),
            "securityReportSha256": security_evidence["reportSha256"],
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
    if schema_version >= 2:
        document["nonce"] = "d" * 64
        document["repository"]["baseRef"] = "main"
        document["evidence"].update(
            {
                "securityManifestPath": str(security_evidence["manifestPath"]),
                "securityManifestSha256": security_evidence["manifestSha256"],
                "securityCoveragePath": str(security_evidence["coveragePath"]),
                "securityCoverageSha256": security_evidence["coverageSha256"],
                "securityFindingsPath": str(security_evidence["findingsPath"]),
                "securityFindingsSha256": security_evidence["findingsSha256"],
            }
        )
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


def _write_security_scan_evidence(directory: Path, head_sha: str) -> dict[str, str]:
    report_path = directory / "security-report.md"
    coverage_path = directory / "coverage.json"
    findings_path = directory / "findings.json"
    manifest_path = directory / "scan-manifest.json"
    report_path.write_text("SECURITY_SCAN_COMPLETE\n", encoding="utf-8")
    coverage_path.write_text(
        json.dumps({"completeness": "complete", "scanId": "scan-v2-test"}, sort_keys=True),
        encoding="utf-8",
    )
    findings_path.write_text(json.dumps({"findings": []}, sort_keys=True), encoding="utf-8")
    coverage_sha = hashlib.sha256(coverage_path.read_bytes()).hexdigest()
    findings_sha = hashlib.sha256(findings_path.read_bytes()).hexdigest()
    manifest_path.write_text(
        json.dumps(
            {
                "scan": {
                    "artifacts": [
                        {"path": "coverage.json", "sha256": coverage_sha},
                        {"path": "findings.json", "sha256": findings_sha},
                    ],
                    "coverageRef": "coverage.json",
                    "findingsRef": "findings.json",
                    "id": "scan-v2-test",
                    "status": "completed",
                    "target": {"kind": "git_revision", "revision": head_sha},
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return {
        "reportPath": str(report_path),
        "reportSha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "manifestPath": str(manifest_path),
        "manifestSha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "coveragePath": str(coverage_path),
        "coverageSha256": coverage_sha,
        "findingsPath": str(findings_path),
        "findingsSha256": findings_sha,
    }


def _packet_digest(document: dict[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("packetSha256", None)
    return hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
