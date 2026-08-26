"""P1 KIS_MOCK 외부 실행 approval을 owner-only 파일로 작성한다."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from app.data._shared.canonical_json import canonical_json_bytes
from app.verification.execution_approval import author_execution_approval


class ApprovalAuthorError(RuntimeError):
    """출력 경계나 서명 입력이 안전하지 않을 때 사용한다."""


def _publish_new(path: Path, payload: bytes) -> None:
    """owner-private directory에 기존 파일을 덮어쓰지 않고 mode 0600으로 게시한다."""

    if not path.is_absolute() or len(payload) > 32 * 1024:
        raise ApprovalAuthorError("P1_EXECUTION_APPROVAL_OUTPUT_INVALID")
    parent = path.parent
    metadata = parent.stat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise ApprovalAuthorError("P1_EXECUTION_APPROVAL_OUTPUT_INVALID")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
        current = os.fstat(descriptor)
        if not stat.S_ISREG(current.st_mode) or stat.S_IMODE(current.st_mode) != 0o600:
            raise ApprovalAuthorError("P1_EXECUTION_APPROVAL_OUTPUT_INVALID")
    finally:
        os.close(descriptor)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="p1-execution-approval-author")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--issuer-key-id", required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--provider-family", required=True)
    parser.add_argument("--operation", action="append", required=True)
    parser.add_argument("--payload-sha256", required=True)
    parser.add_argument("--repository-digest", required=True)
    parser.add_argument("--evidence-digest", required=True)
    parser.add_argument("--owner-scope-digest", required=True)
    parser.add_argument("--account-scope-digest", required=True)
    parser.add_argument("--credential-scope-digest", required=True)
    parser.add_argument("--physical-call-cap", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        approval = author_execution_approval(
            approval_id=args.approval_id,
            issuer_key_id=args.issuer_key_id,
            private_key_path=args.private_key,
            provider_family=args.provider_family,
            exact_operations=args.operation,
            payload_sha256=args.payload_sha256,
            repository_digest=args.repository_digest,
            evidence_digest=args.evidence_digest,
            owner_scope_digest=args.owner_scope_digest,
            account_scope_digest=args.account_scope_digest,
            credential_scope_digest=args.credential_scope_digest,
            physical_call_cap=args.physical_call_cap,
            cost_cap_microusd=0,
            now=datetime.now(UTC),
        )
        _publish_new(args.output, canonical_json_bytes(approval.to_dict()))
    except (OSError, ValueError, ApprovalAuthorError):
        print("P1_EXECUTION_APPROVAL_AUTHOR_REJECTED", file=sys.stderr)
        return 2
    print(f"P1_EXECUTION_APPROVAL_ID={approval.approval_id}")
    print(f"P1_EXECUTION_APPROVAL_SHA256={approval.packet_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
