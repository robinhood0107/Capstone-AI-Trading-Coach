"""One-command KIS mock-account order certification.

The outer Ed25519 packet binds the clean PR evidence, owner-selected scope and
all eight provider operations before any credential-bearing client exists.
The derived lower-limit order price is intentionally produced only after that
packet is consumed once in PostgreSQL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, NoReturn, cast
from uuid import uuid4

from app.brokerage.kis_mock_approval_probe import (
    _V3_CANONICAL_STEPS,
    _V3_SIGNED_OPERATIONS,
    _KISMockProbeOperations,
    KISMockApprovalPacketV3,
    KISMockApprovalRejected,
    KISMockProbeFailed,
    _probe_failure,
)
from app.brokerage.kis_mock_certification_gate import (
    CertificationWindowClosed,
    require_certification_window,
)
from app.data._shared.canonical_json import canonical_json_bytes, canonical_json_sha256
from app.data.kis.accounting import (
    CollectionRunRecorder,
    CollectionRunStatus,
    LogicalOperation,
    PhysicalChannel,
)
from app.data.kis.http_client import CURRENT_PRICE_PATH, KISHttpClient
from app.data.kis.settings import KISSettings
from app.verification.execution_approval import (
    ZERO_SCOPE_SHA256,
    ExecutionApprovalError,
    author_execution_approval,
    load_and_verify_execution_approval,
    scope_digest,
)
from app.verification.provider_claim import (
    ProviderApprovalClaimError,
    claim_signed_provider_approval,
)

_REQUEST_PATH: Final = Path("/certification/certification-request.json")
_APPROVAL_PATH: Final = Path("/certification/execution-approval.json")
_RECEIPT_PATH: Final = Path("/certification/certification.json")
_PRIVATE_KEY_PATH: Final = Path("/run/secrets/kis_mock_approval_private")
_TRUST_POLICY_PATH: Final = Path("/run/secrets/kis_mock_approval_trust_policy")
_ISSUER_KEY_ID: Final = "CAPSTONE.KISMOCK.V1"
_PROVIDER_FAMILY: Final = "KIS_MOCK"
_MAX_FILE_BYTES: Final = 32 * 1024
_HEAD = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BRANCH = re.compile(r"^(?:feature|fix|docs|infra|experiment|codex)/[A-Za-z0-9._/-]{1,120}$")
_REQUIRED_CHECKS: Final = frozenset(
    {
        "Contract schema validation",
        "Spring OpenAPI drift",
        "Kotlin ktlint and build",
        "Python quality gates",
        "Repo hygiene",
        "P1 full-app security gates",
    }
)


class KISMockCertificationError(RuntimeError):
    """A content-free certification gate failure."""


def _read_request(path: Path = _REQUEST_PATH) -> dict[str, object]:
    content = _read_owner_file(path)
    try:
        value: object = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise KISMockCertificationError("KIS_MOCK_CERTIFICATION_REQUEST_INVALID") from error
    if not isinstance(value, dict) or canonical_json_bytes(value) != content:
        raise KISMockCertificationError("KIS_MOCK_CERTIFICATION_REQUEST_INVALID")
    expected = {
        "branch",
        "commitSha",
        "pullRequest",
        "quantity",
        "requiredChecks",
        "securityEvidenceDigest",
        "symbol",
    }
    checks = value.get("requiredChecks")
    if (
        set(value) != expected
        or not isinstance(value.get("branch"), str)
        or _BRANCH.fullmatch(cast(str, value["branch"])) is None
        or not isinstance(value.get("commitSha"), str)
        or _HEAD.fullmatch(cast(str, value["commitSha"])) is None
        or type(value.get("pullRequest")) is not int
        or cast(int, value["pullRequest"]) < 1
        or value.get("symbol") != "005930"
        or value.get("quantity") != 1
        or not isinstance(value.get("securityEvidenceDigest"), str)
        or _SHA256.fullmatch(cast(str, value["securityEvidenceDigest"])) is None
        or not isinstance(checks, list)
        or not all(isinstance(item, str) for item in checks)
        or set(cast(list[str], checks)) != _REQUIRED_CHECKS
        or len(cast(list[str], checks)) != len(_REQUIRED_CHECKS)
    ):
        raise KISMockCertificationError("KIS_MOCK_CERTIFICATION_REQUEST_INVALID")
    return cast(dict[str, object], value)


def author(request_path: Path = _REQUEST_PATH, output_path: Path = _APPROVAL_PATH) -> str:
    request = _read_request(request_path)
    account_id = _required_environment("KIS_MOCK_BOUND_ACCOUNT_ID")
    approval = author_execution_approval(
        approval_id=f"KISMOCK.CERT.{cast(str, request['commitSha'])[:12].upper()}.{secrets.token_hex(4).upper()}",
        issuer_key_id=_ISSUER_KEY_ID,
        private_key_path=_PRIVATE_KEY_PATH,
        provider_family=_PROVIDER_FAMILY,
        exact_operations=_V3_SIGNED_OPERATIONS,
        payload_sha256=canonical_json_sha256(request),
        repository_digest=_repository_digest(request),
        evidence_digest=cast(str, request["securityEvidenceDigest"]),
        owner_scope_digest=ZERO_SCOPE_SHA256,
        account_scope_digest=scope_digest(account_id),
        credential_scope_digest=scope_digest(_PROVIDER_FAMILY),
        physical_call_cap=9,
        cost_cap_microusd=0,
        now=datetime.now(UTC),
    )
    _publish_new(output_path, canonical_json_bytes(approval.to_dict()))
    return approval.approval_id


def certify(
    request_path: Path = _REQUEST_PATH,
    approval_path: Path = _APPROVAL_PATH,
    receipt_path: Path = _RECEIPT_PATH,
) -> dict[str, object]:
    try:
        require_certification_window(datetime.now(UTC))
    except CertificationWindowClosed as error:
        raise KISMockCertificationError(str(error)) from error
    request = _read_request(request_path)
    now = datetime.now(UTC)
    account_id = _required_environment("KIS_MOCK_BOUND_ACCOUNT_ID")
    try:
        approval = load_and_verify_execution_approval(
            approval_path,
            provider_family=_PROVIDER_FAMILY,
            exact_operations=_V3_SIGNED_OPERATIONS,
            payload_sha256=canonical_json_sha256(request),
            repository_digest=_repository_digest(request),
            evidence_digest=cast(str, request["securityEvidenceDigest"]),
            owner_scope_digest=ZERO_SCOPE_SHA256,
            account_scope_digest=scope_digest(account_id),
            credential_scope_digest=scope_digest(_PROVIDER_FAMILY),
            physical_call_cap=9,
            cost_cap_microusd=0,
            now=now,
            trust_policy_path=_TRUST_POLICY_PATH,
            trust_policy_owner_uid=os.geteuid(),
        )
        claim_signed_provider_approval(approval)
    except (ExecutionApprovalError, ProviderApprovalClaimError, ValueError) as error:
        raise KISMockCertificationError("KIS_MOCK_CERTIFICATION_APPROVAL_REJECTED") from error

    quote_counts = {"marketData": 0, "tokenP": 0}
    brokerage_counts = {"brokerage": 0, "tokenP": 0}
    try:
        lower_limit, quote_counts = _read_lower_limit(
            cast(str, request["symbol"]), approval.expires_at
        )
        packet = _runtime_packet(request, approval, account_id, lower_limit)
        brokerage_counts = _run_brokerage(packet, approval.expires_at)
        physical_calls = _combined_counts(quote_counts, brokerage_counts)
        receipt = {
            "commitSha": request["commitSha"],
            "inputSha256": canonical_json_sha256(request),
            "physicalCalls": physical_calls,
            "status": "PASS",
            "timestamp": _instant(datetime.now(UTC)),
        }
        _publish_new(receipt_path, canonical_json_bytes(receipt))
        return receipt
    except KISMockProbeFailed as error:
        physical_calls = {
            "brokerage": error.physical_reservations.get("brokerage", 0),
            "quote": quote_counts.get("marketData", 0),
            "token": quote_counts.get("tokenP", 0) + error.physical_reservations.get("tokenP", 0),
        }
        failure = {
            "commitSha": request["commitSha"],
            "inputSha256": canonical_json_sha256(request),
            "physicalCalls": physical_calls,
            "status": "FAIL",
            "timestamp": _instant(datetime.now(UTC)),
        }
        _publish_new(receipt_path, canonical_json_bytes(failure))
        raise
    except Exception as error:
        physical_calls = {
            "brokerage": brokerage_counts.get("brokerage", 0),
            "quote": quote_counts.get("marketData", 0),
            "token": quote_counts.get("tokenP", 0) + brokerage_counts.get("tokenP", 0),
        }
        failure = {
            "commitSha": request["commitSha"],
            "inputSha256": canonical_json_sha256(request),
            "physicalCalls": physical_calls,
            "status": "FAIL",
            "timestamp": _instant(datetime.now(UTC)),
        }
        _publish_new(receipt_path, canonical_json_bytes(failure))
        if isinstance(error, KISMockCertificationError):
            raise
        raise KISMockCertificationError("KIS_MOCK_CERTIFICATION_PROVIDER_FAILED") from error


def _read_lower_limit(symbol: str, expires_at: datetime) -> tuple[int, dict[str, int]]:
    recorder = CollectionRunRecorder(
        run_id=uuid4(),
        started_at=datetime.now(UTC),
        logical_caps={LogicalOperation.CURRENT_PRICE: 1},
        physical_caps={PhysicalChannel.MARKET_DATA: 1, PhysicalChannel.TOKEN_P: 1},
    )
    settings = KISSettings(kis_mode="mock", kis_offline=False, kis_retry_attempts=1)
    client = KISHttpClient(
        settings,
        accounting=recorder,
        deadline_guard=lambda: _require_before(expires_at),
    )
    token = recorder.start_logical(LogicalOperation.CURRENT_PRICE)
    try:
        response = client.request(
            "GET",
            CURRENT_PRICE_PATH,
            settings.current_price_tr_id,
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
        )
        output = response.get("output")
        if not isinstance(output, Mapping):
            raise KISMockCertificationError("KIS_MOCK_PRICE_RESPONSE_INVALID")
        lower_limit = _positive_int(output.get("stck_llam"))
        if lower_limit % _krx_tick(lower_limit) != 0:
            raise KISMockCertificationError("KIS_MOCK_LOWER_LIMIT_INVALID")
        recorder.succeed_logical(token)
    except Exception:
        try:
            recorder.fail_logical(token, _failure_code())
        except Exception:
            pass
        raise
    finally:
        client.close()
    summary = recorder.snapshot(
        completed_at=datetime.now(UTC),
        status=CollectionRunStatus.SUCCESS,
    )
    counts = {item.channel.value: item.attempts for item in summary.physical_attempts}
    if counts["marketData"] != 1 or counts["tokenP"] not in {0, 1}:
        raise KISMockCertificationError("KIS_MOCK_PRICE_CALL_CAP_INVALID")
    return lower_limit, counts


def _run_brokerage(packet: KISMockApprovalPacketV3, expires_at: datetime) -> dict[str, int]:
    try:
        operations = _KISMockProbeOperations(packet)
    except Exception as error:
        raise _probe_failure("runtimeInit", {"tokenP": 0, "brokerage": 0}, error) from None
    try:
        for operation in packet.steps:
            _require_before(expires_at)
            try:
                operations.run(operation, packet)
            except Exception as error:
                raise _probe_failure(operation, operations.counts(), error) from None
        return operations.counts()
    finally:
        operations.close()


def _runtime_packet(
    request: Mapping[str, object],
    approval: Any,
    account_id: str,
    lower_limit: int,
) -> KISMockApprovalPacketV3:
    now = datetime.now(UTC)
    checks = [
        {"name": name, "conclusion": "SUCCESS"}
        for name in cast(list[str], request["requiredChecks"])
    ]
    digest = cast(str, request["securityEvidenceDigest"])
    document: dict[str, object] = {
        "schemaVersion": 3,
        "approvalId": "approval-s3-online-" + secrets.token_hex(16),
        "nonce": secrets.token_hex(32),
        "issuedAt": _instant(now),
        "expiresAt": _instant(approval.expires_at),
        "mode": "KIS_MOCK",
        "kisLiveOrderEnabled": False,
        "retryCount": 0,
        "artifactWrites": 0,
        "providerCallsBeforeApproval": 0,
        "probeType": "FULL",
        "repository": {
            "root": "/certification",
            "branchRef": request["branch"],
            "baseRef": "main",
            "headSha": request["commitSha"],
            "remoteHeadSha": request["commitSha"],
            "pullRequest": request["pullRequest"],
            "evidenceMode": "OPEN_PR",
        },
        "evidence": {
            "ciHeadSha": request["commitSha"],
            "requiredChecks": checks,
            "securityHeadSha": request["commitSha"],
            "securityStatus": "SECURITY_SCAN_COMPLETE",
            "securityFindings": 0,
            "securityReportPath": "/certification/security-report.json",
            "securityReportSha256": digest,
            "securityManifestPath": "/certification/security-manifest.json",
            "securityManifestSha256": digest,
            "securityCoveragePath": "/certification/security-coverage.json",
            "securityCoverageSha256": digest,
            "securityFindingsPath": "/certification/security-findings.json",
            "securityFindingsSha256": digest,
        },
        "physicalCaps": {"tokenP": 1, "brokerage": 7},
        "redisBaseline": {"restPttlMillis": -2, "tokenPttlMillis": -2, "observedAt": _instant(now)},
        "referenceTtlSeconds": 900,
        "order": {
            "orderId": "ord_mock_" + secrets.token_hex(16),
            "accountId": account_id,
            "symbol": request["symbol"],
            "side": "BUY",
            "orderType": "LIMIT",
            "quantity": 1,
            "limitPriceKrw": lower_limit,
            "orderDivision": "00",
            "exchangeDivision": "KRX",
        },
        "execution": {"from": now.date().isoformat(), "to": now.date().isoformat(), "recent": True},
        "steps": list(_V3_CANONICAL_STEPS),
        "stopRule": "FIRST_FAILURE_STOPS_REMAINING_CALLS",
        "executionCommand": "./capstone mock certify --symbol 005930 --quantity 1",
        "packetSha256": "0" * 64,
    }
    unsigned = dict(document)
    unsigned.pop("packetSha256")
    document["packetSha256"] = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    return KISMockApprovalPacketV3.model_validate(document)


def _combined_counts(quote: Mapping[str, int], brokerage: Mapping[str, int]) -> dict[str, int]:
    result = {
        "brokerage": brokerage.get("brokerage", 0),
        "quote": quote.get("marketData", 0),
        "token": quote.get("tokenP", 0) + brokerage.get("tokenP", 0),
    }
    if result["brokerage"] != 7 or result["quote"] != 1 or result["token"] not in {0, 1}:
        raise KISMockCertificationError("KIS_MOCK_PHYSICAL_CALL_CAP_INVALID")
    return result


def _repository_digest(request: Mapping[str, object]) -> str:
    return canonical_json_sha256(
        {
            "branch": request["branch"],
            "commitSha": request["commitSha"],
            "pullRequest": request["pullRequest"],
            "requiredChecks": request["requiredChecks"],
        }
    )


def _read_owner_file(path: Path) -> bytes:
    if not path.is_absolute():
        raise KISMockCertificationError("KIS_MOCK_CERTIFICATION_FILE_INVALID")
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise KISMockCertificationError("KIS_MOCK_CERTIFICATION_FILE_UNAVAILABLE") from error
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or not 1 <= info.st_size <= _MAX_FILE_BYTES
        ):
            raise KISMockCertificationError("KIS_MOCK_CERTIFICATION_FILE_INVALID")
        content = os.read(descriptor, _MAX_FILE_BYTES + 1)
        if len(content) > _MAX_FILE_BYTES or os.read(descriptor, 1):
            raise KISMockCertificationError("KIS_MOCK_CERTIFICATION_FILE_INVALID")
        return content
    finally:
        os.close(descriptor)


def _publish_new(path: Path, payload: bytes) -> None:
    if not path.is_absolute() or len(payload) > _MAX_FILE_BYTES:
        raise KISMockCertificationError("KIS_MOCK_CERTIFICATION_OUTPUT_INVALID")
    parent = path.parent.stat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) & 0o077
    ):
        raise KISMockCertificationError("KIS_MOCK_CERTIFICATION_OUTPUT_INVALID")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise KISMockCertificationError("KIS_MOCK_CERTIFICATION_OUTPUT_INVALID")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise KISMockCertificationError("KIS_MOCK_CERTIFICATION_SECRET_UNAVAILABLE")
    return value


def _positive_int(value: object) -> int:
    try:
        parsed = int(str(value).replace(",", ""))
    except (TypeError, ValueError) as error:
        raise KISMockCertificationError("KIS_MOCK_PRICE_RESPONSE_INVALID") from error
    if parsed <= 0:
        raise KISMockCertificationError("KIS_MOCK_PRICE_RESPONSE_INVALID")
    return parsed


def _krx_tick(price: int) -> int:
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def _require_before(expires_at: datetime) -> None:
    if datetime.now(UTC) >= expires_at:
        raise KISMockCertificationError("KIS_MOCK_CERTIFICATION_APPROVAL_EXPIRED")


def _failure_code() -> Any:
    from app.data.kis.accounting import FailureCode

    return FailureCode.UNKNOWN_INTERNAL


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _reject(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(2)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kis-mock-certification")
    parser.add_argument("command", choices=("author", "run"))
    args = parser.parse_args(argv)
    try:
        if args.command == "author":
            approval_id = author()
            print(f"KIS_MOCK_APPROVAL_AUTHOR=PASS ID={approval_id}")
            return 0
        receipt = certify()
    except KISMockCertificationError as error:
        print(str(error), file=sys.stderr)
        return 2
    except KISMockProbeFailed as error:
        print(
            "KIS_MOCK_CERTIFICATION_PROVIDER_FAILED "
            f"step={error.failed_step} reason={error.reason_code} "
            f"brokerage={error.physical_reservations.get('brokerage', 0)} "
            f"token={error.physical_reservations.get('tokenP', 0)}",
            file=sys.stderr,
        )
        return 1
    except KISMockApprovalRejected:
        print("KIS_MOCK_CERTIFICATION_PROVIDER_FAILED", file=sys.stderr)
        return 1
    print("KIS_MOCK_CERTIFICATION=PASS")
    print(
        "KIS_MOCK_PHYSICAL_CALLS="
        + json.dumps(receipt["physicalCalls"], sort_keys=True, separators=(",", ":"))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
