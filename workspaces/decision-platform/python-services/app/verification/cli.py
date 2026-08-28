"""Internal CLI for P1 verification packet authoring and provider-free execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from app.data._shared.repository_root import repository_root

from app.verification.artifacts import publish_packet, publish_report, read_packet, read_report
from app.verification.packet import (
    P1SignedApprovalPacket,
    author_signed_provider_read_smoke_packet,
    verify_signed_repository_binding,
)
from app.verification.runner import run_s0_s5_current

_REPOSITORY_ROOT = repository_root(__file__, 5)
_APPROVAL_TRUST_POLICY = Path("/etc/capstone-p1/approval-trust-root.json")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="p1-verify")
    subparsers = parser.add_subparsers(dest="command", required=True)

    author = subparsers.add_parser("author")
    author.add_argument("--approval-id", required=True)
    author.add_argument("--output-root", type=Path, required=True)
    author.add_argument("--kis-token-cap", type=int, choices=(0, 1), required=True)
    author.add_argument("--private-key", type=Path, required=True)
    author.add_argument("--issuer-key-id", required=True)
    author.add_argument("--reason-code", required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--profile", choices=("S0_S5_CURRENT", "PROVIDER_READ_SMOKE"), required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--packet", type=Path)

    verify = subparsers.add_parser("verify")
    verify.add_argument("artifact", type=Path)

    args = parser.parse_args(argv)
    if args.command == "author":
        _require_external_output_root(args.output_root)
        signed_packet = author_signed_provider_read_smoke_packet(
            repository_root=_REPOSITORY_ROOT,
            approval_id=args.approval_id,
            private_key_path=args.private_key,
            issuer_key_id=args.issuer_key_id,
            reason_code=args.reason_code,
            now=datetime.now(UTC),
            kis_token_physical_call_cap=args.kis_token_cap,
        )
        path = publish_packet(args.output_root, signed_packet)
        print(f"P1_VERIFICATION_PACKET={signed_packet.packet_sha256} PATH={path}")
        return 0
    if args.command == "run":
        _require_external_output_root(args.output_root)
        if args.profile == "PROVIDER_READ_SMOKE":
            # Keep every credential-capable provider module unloaded until after the
            # packet and protected trust root have been parsed successfully.
            from app.verification.provider_smoke import run_provider_read_smoke

            if args.packet is None:
                parser.error("PROVIDER_READ_SMOKE requires --packet")
            loaded_packet = read_packet(args.packet.absolute())
            if not isinstance(loaded_packet, P1SignedApprovalPacket):
                raise ValueError("P1 verification packet v1 has no execution authority")
            public_key, issuer_key_id, public_key_sha256 = _approval_trust_anchor()
            report = run_provider_read_smoke(
                repository_root=_REPOSITORY_ROOT,
                output_root=args.output_root,
                packet=loaded_packet,
                binding_verifier=lambda value, root: verify_signed_repository_binding(
                    value,
                    root,
                    public_key,
                    expected_issuer_key_id=issuer_key_id,
                    expected_public_key_sha256=public_key_sha256,
                ),
            )
            path = publish_report(args.output_root, report)
            print(f"P1_VERIFICATION_REPORT={report.to_dict()['evidenceSha256']} PATH={path}")
            return 0 if report.execution_state == "PASS" else 1
        if args.packet is not None:
            parser.error("S0_S5_CURRENT does not accept a provider packet")
        report = run_s0_s5_current(repository_root=_REPOSITORY_ROOT)
        path = publish_report(args.output_root, report)
        print(f"P1_VERIFICATION_REPORT={report.to_dict()['evidenceSha256']} PATH={path}")
        return 0 if report.execution_state == "PASS" else 1
    artifact = args.artifact
    current_head = _current_clean_head()
    if artifact.name.startswith("packet-"):
        loaded_packet = read_packet(artifact)
        if loaded_packet.head_sha != current_head:
            raise ValueError("P1 verification packet does not match the current clean HEAD")
        if isinstance(loaded_packet, P1SignedApprovalPacket):
            public_key, issuer_key_id, public_key_sha256 = _approval_trust_anchor()
            verify_signed_repository_binding(
                loaded_packet,
                _REPOSITORY_ROOT,
                public_key,
                expected_issuer_key_id=issuer_key_id,
                expected_public_key_sha256=public_key_sha256,
            )
        print(
            "P1_VERIFICATION_PACKET=STRUCTURALLY_VALID_CURRENT_HEAD "
            f"SHA256={loaded_packet.packet_sha256}"
        )
    else:
        report = read_report(artifact)
        if report.head_sha != current_head:
            raise ValueError("P1 verification report does not match the current clean HEAD")
        print(
            "P1_VERIFICATION_REPORT=STRUCTURALLY_VALID_CURRENT_HEAD "
            f"OUTCOME={report.aggregate_outcome} PROFILE={report.profile}"
        )
    return 0


def _approval_trust_anchor(
    policy_path: Path = _APPROVAL_TRUST_POLICY,
    *,
    expected_owner_uid: int = 0,
) -> tuple[Path, str, str]:
    policy_bytes = _read_protected_policy_file(
        policy_path,
        expected_owner_uid=expected_owner_uid,
        max_bytes=4_096,
    )
    try:
        policy = json.loads(policy_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("P1 approval trust policy is invalid") from error
    if (
        not isinstance(policy, dict)
        or set(policy) != {"contractId", "issuerKeyId", "publicKeyPath", "publicKeySha256"}
        or policy.get("contractId") != "p1-approval-trust-root.v1"
    ):
        raise ValueError("P1 approval trust policy is not closed")
    path_value = policy.get("publicKeyPath")
    issuer_key_id = policy.get("issuerKeyId")
    digest = policy.get("publicKeySha256")
    if not isinstance(path_value, str) or not Path(path_value).is_absolute():
        raise ValueError("P1 approval public key path is not pinned")
    path = Path(path_value)
    if (
        not isinstance(issuer_key_id, str)
        or re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{2,63}", issuer_key_id) is None
    ):
        raise ValueError("P1 approval issuer key id is not pinned")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("P1 approval public key digest is not pinned")

    public_key = _read_protected_policy_file(
        path,
        expected_owner_uid=expected_owner_uid,
        max_bytes=8_192,
    )
    if hashlib.sha256(public_key).hexdigest() != digest:
        raise ValueError("P1 approval public key does not match trust policy")
    return path, issuer_key_id, digest


def _read_protected_policy_file(path: Path, *, expected_owner_uid: int, max_bytes: int) -> bytes:
    if not path.is_absolute():
        raise ValueError("P1 approval trust path must be absolute")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError("P1 approval trust file is unavailable") from error
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != expected_owner_uid
            or stat.S_IMODE(info.st_mode) & 0o022
            or info.st_size < 1
            or info.st_size > max_bytes
        ):
            raise ValueError("P1 approval trust file boundary is invalid")
        content = os.read(descriptor, max_bytes + 1)
        if len(content) > max_bytes or os.read(descriptor, 1):
            raise ValueError("P1 approval trust file exceeds size limit")
        return content
    finally:
        os.close(descriptor)


def _require_external_output_root(output_root: Path) -> None:
    absolute = output_root.absolute()
    try:
        absolute.relative_to(_REPOSITORY_ROOT)
    except ValueError:
        return
    raise ValueError("P1 verification artifacts must remain outside the Git repository")


def _current_clean_head() -> str:
    head = subprocess.run(
        ["/usr/bin/git", "rev-parse", "HEAD"],
        cwd=_REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["/usr/bin/git", "status", "--porcelain", "--untracked-files=all"],
        cwd=_REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if dirty:
        raise ValueError("P1 verification requires a clean worktree")
    return head


if __name__ == "__main__":
    raise SystemExit(main())
