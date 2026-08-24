"""current PR evidence를 읽어 one-time KIS_MOCK v2 approval packet만 author한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NoReturn

from pydantic import SecretStr

from app.brokerage.kis_mock_approval_environment import (
    KISMockApprovalEnvironmentRejected,
    load_kis_mock_approval_environment,
)
from app.brokerage.kis_mock_approval_probe import (
    _REQUIRED_CI_CHECKS,
    KISMockApprovalPacketV2,
    KISMockApprovalPacketV3,
    _canonical_json,
    _git_revision,
    _require_clean_repository,
    _validate_v2_security_evidence,
)
from app.brokerage.mock_order_reference_store import (
    EncryptedRedisApprovalOutcomeStore,
    KISMockApprovalOutcomeUnavailable,
)
from app.data.kis._credential_transport import _build_redis_client, _provider_scope

_REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
_MAX_PACKET_BYTES = 64 * 1024


class KISMockApprovalAuthorRejected(RuntimeError):
    """operator 입력이나 local GitHub/Redis evidence가 exact contract와 다르면 author를 거부한다."""


def write_new_approval_packet(packet_path: Path, payload: bytes) -> None:
    """owner-only directory에 새 regular packet만 dirfd/O_NOFOLLOW/O_EXCL로 publish한다."""

    if len(payload) > _MAX_PACKET_BYTES:
        raise KISMockApprovalAuthorRejected("approval packet output is invalid")
    parent_fd = _open_owner_private_directory(packet_path.parent)
    descriptor: int | None = None
    try:
        name = packet_path.name
        if name in {"", ".", ".."} or "/" in name:
            raise KISMockApprovalAuthorRejected("approval packet output is invalid")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=parent_fd)
        except OSError:
            raise KISMockApprovalAuthorRejected("approval packet output is unavailable") from None
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
            ):
                raise KISMockApprovalAuthorRejected("approval packet output is invalid")
            _write_all(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
            descriptor = None
        published = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(published.st_mode)
            or stat.S_IMODE(published.st_mode) != 0o600
            or published.st_uid != os.getuid()
            or published.st_nlink != 1
        ):
            raise KISMockApprovalAuthorRejected("approval packet output is invalid")
        os.fsync(parent_fd)
    except OSError:
        raise KISMockApprovalAuthorRejected("approval packet output is unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)


def _open_owner_private_directory(directory: Path) -> int:
    """symlinked parent 경로를 거부하고 final packet directory만 operator private mode로 허용한다."""

    if not directory.is_absolute() or any(part in {"", ".", ".."} for part in directory.parts):
        raise KISMockApprovalAuthorRejected("approval packet output is invalid")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open("/", flags)
        for component in directory.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        metadata = os.fstat(descriptor)
    except OSError:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        raise KISMockApprovalAuthorRejected("approval packet output is unavailable") from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        os.close(descriptor)
        raise KISMockApprovalAuthorRejected("approval packet output is invalid")
    return descriptor


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("packet write failed")
        remaining = remaining[written:]


def _capture_redis_baseline(observed_at: datetime) -> dict[str, object]:
    """실제 online runtime과 같은 opaque limiter keys의 PTTL만 provider 호출 없이 snapshot한다."""

    redis_client: Any | None = None
    try:
        redis_client = _build_redis_client()
        scope = _provider_scope("mock")
        rest_pttl = redis_client.pttl(f"kis:rest:v3:{scope}")
        token_pttl = redis_client.pttl("kis:tokenp:v3:deployment")
    except Exception:
        raise KISMockApprovalAuthorRejected("Redis baseline is unavailable") from None
    finally:
        if redis_client is not None:
            redis_client.close()
    if type(rest_pttl) is not int or type(token_pttl) is not int:
        raise KISMockApprovalAuthorRejected("Redis baseline is invalid")
    return {
        "restPttlMillis": rest_pttl,
        "tokenPttlMillis": token_pttl,
        "observedAt": _utc_text(observed_at),
    }


def _collect_current_pr_evidence(
    repository_root: Path,
    *,
    pull_request: int,
) -> tuple[str, str, list[dict[str, str]]]:
    """GitHub PR head/base/checks를 직접 읽어 operator가 SHA나 check 결과를 주입하지 못하게 한다."""

    _require_clean_repository(repository_root)
    head = _git_revision(repository_root, "HEAD")
    try:
        branch_result = subprocess.run(
            ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        branch = branch_result.stdout.strip()
        remote_head = _git_revision(repository_root, f"refs/remotes/origin/{branch}")
        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(pull_request),
                "--json",
                "number,state,isDraft,headRefName,baseRefName,headRefOid,statusCheckRollup",
            ],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        raw: object = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        raise KISMockApprovalAuthorRejected("PR evidence is unavailable") from None
    if not isinstance(raw, dict):
        raise KISMockApprovalAuthorRejected("PR evidence is invalid")
    if raw.get("state") != "OPEN" or raw.get("isDraft") is not False:
        raise KISMockApprovalAuthorRejected("PR evidence is not active")
    if (
        raw.get("number") != pull_request
        or raw.get("headRefName") != branch
        or raw.get("baseRefName") != "main"
        or raw.get("headRefOid") != head
        or remote_head != head
    ):
        raise KISMockApprovalAuthorRejected("PR evidence does not bind one final HEAD")
    rollup = raw.get("statusCheckRollup")
    if not isinstance(rollup, list):
        raise KISMockApprovalAuthorRejected("PR checks are unavailable")
    checks: list[dict[str, str]] = []
    for item in rollup:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        conclusion = item.get("conclusion")
        if isinstance(name, str) and isinstance(conclusion, str):
            checks.append({"name": name, "conclusion": conclusion})
    check_by_name = {entry["name"]: entry["conclusion"] for entry in checks}
    if any(check_by_name.get(name) != "SUCCESS" for name in _REQUIRED_CI_CHECKS):
        raise KISMockApprovalAuthorRejected("PR required checks are incomplete")
    selected_checks = [
        {"name": name, "conclusion": "SUCCESS"} for name in sorted(_REQUIRED_CI_CHECKS)
    ]
    return branch, head, selected_checks


def _collect_merged_main_evidence(
    repository_root: Path,
    *,
    pull_request: int,
) -> tuple[str, str, list[dict[str, str]]]:
    """clean origin/main, merged implementation PR와 exact merge SHA post-merge CI를 직접 검증한다."""

    _require_clean_repository(repository_root)
    head = _git_revision(repository_root, "HEAD")
    remote_head = _git_revision(repository_root, "refs/remotes/origin/main")
    try:
        branch_result = subprocess.run(
            ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        branch = branch_result.stdout.strip()
        pull_request_result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(pull_request),
                "--json",
                "number,state,isDraft,baseRefName,mergeCommit",
            ],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        main_ref_result = subprocess.run(
            [
                "gh",
                "api",
                "--method",
                "GET",
                "repos/{owner}/{repo}/git/ref/heads/main",
            ],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        checks_result = subprocess.run(
            [
                "gh",
                "api",
                "--method",
                "GET",
                f"repos/{{owner}}/{{repo}}/commits/{head}/check-runs",
                "-f",
                "filter=latest",
                "-f",
                "per_page=100",
            ],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        pull_request_document: object = json.loads(pull_request_result.stdout)
        main_ref_document: object = json.loads(main_ref_result.stdout)
        checks_document: object = json.loads(checks_result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        raise KISMockApprovalAuthorRejected("merged main evidence is unavailable") from None
    if branch != "main" or head != remote_head:
        raise KISMockApprovalAuthorRejected("merged main evidence does not bind origin/main")
    if not isinstance(main_ref_document, dict):
        raise KISMockApprovalAuthorRejected("remote main evidence is invalid")
    main_ref_object = main_ref_document.get("object")
    if not isinstance(main_ref_object, dict) or main_ref_object.get("sha") != head:
        raise KISMockApprovalAuthorRejected("remote main does not bind one final HEAD")
    if not isinstance(pull_request_document, dict):
        raise KISMockApprovalAuthorRejected("merged main evidence is invalid")
    merge_commit = pull_request_document.get("mergeCommit")
    if (
        pull_request_document.get("number") != pull_request
        or pull_request_document.get("state") != "MERGED"
        or pull_request_document.get("isDraft") is not False
        or pull_request_document.get("baseRefName") != "main"
        or not isinstance(merge_commit, dict)
        or merge_commit.get("oid") != head
    ):
        raise KISMockApprovalAuthorRejected("merged PR does not bind one final HEAD")
    successful_names = _successful_post_merge_check_names(checks_document, head=head)
    if not _REQUIRED_CI_CHECKS.issubset(successful_names):
        raise KISMockApprovalAuthorRejected("post-merge required checks are incomplete")
    selected_checks = [
        {"name": name, "conclusion": "SUCCESS"} for name in sorted(_REQUIRED_CI_CHECKS)
    ]
    return branch, head, selected_checks


def _successful_post_merge_check_names(document: object, *, head: str) -> frozenset[str]:
    """GitHub check-runs 중 exact merge SHA에서 성공한 required job 이름만 투영한다."""

    if not isinstance(document, dict):
        return frozenset()
    check_runs = document.get("check_runs")
    if not isinstance(check_runs, list):
        return frozenset()
    return frozenset(
        name
        for item in check_runs
        if isinstance(item, dict)
        and item.get("head_sha") == head
        and item.get("status") == "completed"
        and item.get("conclusion") == "success"
        and isinstance((app := item.get("app")), dict)
        and app.get("slug") == "github-actions"
        and isinstance((name := item.get("name")), str)
    )


def _require_recovery_source_outcome(
    recovery_of: dict[str, str],
    *,
    order_id: str,
    account_id: str,
    reference_ttl_seconds: int,
) -> None:
    """CANCEL_RECOVERY author는 operator CLI 입력이 아닌 source executor receipt만 신뢰한다."""

    try:
        encryption_key = load_kis_mock_approval_environment("KIS_MOCK_ORDER_REFERENCE_KEY")[
            "KIS_MOCK_ORDER_REFERENCE_KEY"
        ]
    except KISMockApprovalEnvironmentRejected:
        raise KISMockApprovalAuthorRejected("recovery source outcome is unavailable") from None
    redis_client: Any | None = None
    try:
        redis_client = _build_redis_client()
        store = EncryptedRedisApprovalOutcomeStore(
            redis_client,
            encryption_key=SecretStr(encryption_key),
            ttl_seconds=reference_ttl_seconds,
        )
        store.require_recovery(
            source_approval_id=recovery_of["sourceApprovalId"],
            source_packet_sha256=recovery_of["sourcePacketSha256"],
            source_nonce=recovery_of["sourceNonce"],
            expected_failed_step=recovery_of["failedStep"],  # type: ignore[arg-type]
            order_id=order_id,
            account_id=account_id,
        )
    except (KeyError, KISMockApprovalOutcomeUnavailable, ValueError):
        raise KISMockApprovalAuthorRejected("recovery source outcome is unavailable") from None
    except Exception:
        raise KISMockApprovalAuthorRejected("recovery source outcome is unavailable") from None
    finally:
        encryption_key = ""
        if redis_client is not None:
            redis_client.close()


def author_v2_packet(
    *,
    repository_root: Path,
    output_path: Path,
    approval_id: str,
    nonce: str,
    issued_at: datetime,
    expires_at: datetime,
    pull_request: int,
    execution_head_mode: str,
    security_report_path: Path,
    security_manifest_path: Path,
    security_coverage_path: Path,
    security_findings_path: Path,
    order_id: str,
    account_id: str,
    symbol: str,
    limit_price_krw: int,
    execution_start: str,
    execution_end: str,
    reference_ttl_seconds: int,
    probe_type: str,
    recovery_of: dict[str, str] | None,
    packet_schema_version: Literal[2, 3] = 2,
) -> tuple[str, str]:
    """current GitHub/Redis evidence로 v2 legacy 또는 v3 final packet bytes를 만든다."""

    resolved_root = repository_root.resolve(strict=True)
    if execution_head_mode == "OPEN_PR":
        branch, head, checks = _collect_current_pr_evidence(
            resolved_root,
            pull_request=pull_request,
        )
    elif execution_head_mode == "MERGED_MAIN":
        branch, head, checks = _collect_merged_main_evidence(
            resolved_root,
            pull_request=pull_request,
        )
    else:
        raise KISMockApprovalAuthorRejected("execution HEAD mode is invalid")
    report, report_sha256 = _security_evidence_file(security_report_path)
    manifest, manifest_sha256 = _security_evidence_file(security_manifest_path)
    coverage, coverage_sha256 = _security_evidence_file(security_coverage_path)
    findings, findings_sha256 = _security_evidence_file(security_findings_path)
    observation = datetime.now(tz=UTC)
    baseline = _capture_redis_baseline(observation)
    expected_steps, brokerage_cap = _profile_steps_and_cap(
        probe_type,
        recovery_of,
        schema_version=packet_schema_version,
    )
    if probe_type == "CANCEL_RECOVERY" and recovery_of is not None:
        _require_recovery_source_outcome(
            recovery_of,
            order_id=order_id,
            account_id=account_id,
            reference_ttl_seconds=reference_ttl_seconds,
        )
    document: dict[str, object] = {
        "schemaVersion": packet_schema_version,
        "approvalId": approval_id,
        "nonce": nonce,
        "issuedAt": _utc_text(issued_at),
        "expiresAt": _utc_text(expires_at),
        "mode": "KIS_MOCK",
        "kisLiveOrderEnabled": False,
        "retryCount": 0,
        "artifactWrites": 0,
        "providerCallsBeforeApproval": 0,
        "probeType": probe_type,
        "repository": {
            "root": str(resolved_root),
            "branchRef": branch,
            "baseRef": "main",
            "headSha": head,
            "remoteHeadSha": head,
            "pullRequest": pull_request,
            "evidenceMode": execution_head_mode,
        },
        "evidence": {
            "ciHeadSha": head,
            "requiredChecks": checks,
            "securityHeadSha": head,
            "securityStatus": "SECURITY_SCAN_COMPLETE",
            "securityFindings": 0,
            "securityReportPath": str(report),
            "securityReportSha256": report_sha256,
            "securityManifestPath": str(manifest),
            "securityManifestSha256": manifest_sha256,
            "securityCoveragePath": str(coverage),
            "securityCoverageSha256": coverage_sha256,
            "securityFindingsPath": str(findings),
            "securityFindingsSha256": findings_sha256,
        },
        "physicalCaps": {"tokenP": 1, "brokerage": brokerage_cap},
        "redisBaseline": baseline,
        "referenceTtlSeconds": reference_ttl_seconds,
        "order": {
            "orderId": order_id,
            "accountId": account_id,
            "symbol": symbol,
            "side": "BUY",
            "orderType": "LIMIT",
            "quantity": 1,
            "limitPriceKrw": limit_price_krw,
            "orderDivision": "00",
            "exchangeDivision": "KRX",
        },
        "execution": {"from": execution_start, "to": execution_end, "recent": True},
        "steps": list(expected_steps),
        "stopRule": "FIRST_FAILURE_STOPS_REMAINING_CALLS",
        "executionCommand": (
            f"uv run --directory {resolved_root}/workspaces/decision-platform/python-services "
            "--frozen kis-mock-brokerage-probe "
            f"--approval-packet {output_path}"
        ),
        "packetSha256": "0" * 64,
    }
    if recovery_of is not None:
        document["recoveryOf"] = recovery_of
    try:
        packet = _validate_current_packet(document, packet_schema_version)
        _validate_v2_security_evidence(packet.evidence, head)
    except Exception:
        raise KISMockApprovalAuthorRejected("approval packet contract is invalid") from None
    unsigned = dict(document)
    unsigned.pop("packetSha256")
    packet_sha256 = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
    document["packetSha256"] = packet_sha256
    try:
        packet = _validate_current_packet(document, packet_schema_version)
        _validate_v2_security_evidence(packet.evidence, head)
    except Exception:
        raise KISMockApprovalAuthorRejected("approval packet contract is invalid") from None
    write_new_approval_packet(output_path, _canonical_json(document) + b"\n")
    return approval_id, packet_sha256


def _security_evidence_file(path: Path) -> tuple[Path, str]:
    """author 단계에서도 scan artifact raw path를 신뢰하지 않고 regular bounded file digest를 고정한다."""

    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4 * 1024 * 1024:
            raise OSError
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError:
        raise KISMockApprovalAuthorRejected("security scan evidence is unavailable") from None
    return resolved, digest


def _profile_steps_and_cap(
    probe_type: str,
    recovery_of: dict[str, str] | None,
    *,
    schema_version: Literal[2, 3] = 2,
) -> tuple[tuple[str, ...], int]:
    if schema_version == 3:
        if probe_type == "FULL" and recovery_of is None:
            return (
                "preBalance",
                "buyable",
                "submitLimitBuy",
                "cancelFull",
                "executionRead",
                "postBalance",
                "openOrderReconciliation",
            ), 7
        raise KISMockApprovalAuthorRejected("approval packet profile is invalid")
    if probe_type == "FULL" and recovery_of is None:
        return ("balance", "buyable", "submitLimitBuy", "cancelFull", "executionRead"), 5
    if probe_type == "BALANCE_DIAGNOSTIC" and recovery_of is None:
        return ("balance",), 1
    if probe_type == "CANCEL_RECOVERY" and recovery_of is not None:
        failed_step = recovery_of.get("failedStep")
        if failed_step == "cancelFull":
            return ("cancelFull", "executionRead"), 2
        if failed_step == "executionRead":
            return ("executionRead",), 1
    raise KISMockApprovalAuthorRejected("approval packet profile is invalid")


def _validate_current_packet(
    document: dict[str, object],
    schema_version: Literal[2, 3],
) -> KISMockApprovalPacketV2 | KISMockApprovalPacketV3:
    if schema_version == 3:
        return KISMockApprovalPacketV3.model_validate(document)
    return KISMockApprovalPacketV2.model_validate(document)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise KISMockApprovalAuthorRejected("approval timestamp is invalid")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _reject_cli()
    if parsed.tzinfo is None:
        _reject_cli()
    return parsed


def _reject_cli() -> NoReturn:
    raise KISMockApprovalAuthorRejected("approval author input is invalid")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Author one exact KIS_MOCK approval packet")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--issued-at", required=True)
    parser.add_argument("--expires-at", required=True)
    parser.add_argument("--pull-request", type=int, required=True)
    parser.add_argument(
        "--execution-head-mode",
        choices=("OPEN_PR", "MERGED_MAIN"),
        default="OPEN_PR",
    )
    parser.add_argument("--security-report", type=Path, required=True)
    parser.add_argument("--security-manifest", type=Path, required=True)
    parser.add_argument("--security-coverage", type=Path, required=True)
    parser.add_argument("--security-findings", type=Path, required=True)
    parser.add_argument("--order-id", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--limit-price-krw", type=int, required=True)
    parser.add_argument("--execution-from", required=True)
    parser.add_argument("--execution-to", required=True)
    parser.add_argument("--reference-ttl-seconds", type=int, default=900)
    parser.add_argument("--packet-version", type=int, choices=(2, 3), default=2)
    parser.add_argument(
        "--probe-type",
        choices=("FULL", "BALANCE_DIAGNOSTIC", "CANCEL_RECOVERY"),
        default="FULL",
    )
    parser.add_argument("--source-approval-id")
    parser.add_argument("--source-packet-sha256")
    parser.add_argument("--source-nonce")
    parser.add_argument("--source-failed-step", choices=("cancelFull", "executionRead"))
    args = parser.parse_args(argv)
    recovery_values = (
        args.source_approval_id,
        args.source_packet_sha256,
        args.source_nonce,
        args.source_failed_step,
    )
    recovery_of: dict[str, str] | None = None
    if any(value is not None for value in recovery_values):
        if any(value is None for value in recovery_values):
            print("S3_KIS_MOCK_APPROVAL_AUTHOR_REJECTED", file=sys.stderr)
            return 2
        recovery_of = {
            "sourceApprovalId": args.source_approval_id,
            "sourcePacketSha256": args.source_packet_sha256,
            "sourceNonce": args.source_nonce,
            "failedStep": args.source_failed_step,
        }
    try:
        approval_id, packet_sha256 = author_v2_packet(
            repository_root=_REPOSITORY_ROOT,
            output_path=args.output,
            approval_id=args.approval_id,
            nonce=args.nonce,
            issued_at=_parse_timestamp(args.issued_at),
            expires_at=_parse_timestamp(args.expires_at),
            pull_request=args.pull_request,
            execution_head_mode=args.execution_head_mode,
            security_report_path=args.security_report,
            security_manifest_path=args.security_manifest,
            security_coverage_path=args.security_coverage,
            security_findings_path=args.security_findings,
            order_id=args.order_id,
            account_id=args.account_id,
            symbol=args.symbol,
            limit_price_krw=args.limit_price_krw,
            execution_start=args.execution_from,
            execution_end=args.execution_to,
            reference_ttl_seconds=args.reference_ttl_seconds,
            probe_type=args.probe_type,
            recovery_of=recovery_of,
            packet_schema_version=args.packet_version,
        )
    except KISMockApprovalAuthorRejected:
        print("S3_KIS_MOCK_APPROVAL_AUTHOR_REJECTED", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"approvalId": approval_id, "packetSha256": packet_sha256},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
