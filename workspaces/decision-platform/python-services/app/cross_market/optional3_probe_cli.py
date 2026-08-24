"""Local-only CLI for a packet-gated S4.8 Optional 3 provider probe."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from app.cross_market.optional3_probe import (
    Optional3ProbeError,
    Optional3ProbeExecutionBinding,
    Optional3ProbeExecutor,
    Optional3ProbePacket,
    StdlibOptional3ProbeTransport,
    optional3_credential_environment_variable,
)
from app.data._shared.canonical_json import canonical_json_sha256
from app.rag.oa112_downloader import Oa112DownloadError, _read_private_control_file
from app.verification.execution_approval import (
    ExecutionApprovalError,
    load_and_verify_execution_approval,
    scope_digest,
)
from app.verification.provider_claim import (
    ProviderApprovalClaimError,
    claim_signed_provider_approval,
)


_CONTROL_ROOT_RELATIVE: Final[Path] = Path("capstone-rag/secrets/optional3-probes")
_EVIDENCE_FILE: Final[str] = "optional3-probe-execution-evidence.v1.json"
_APPROVAL_FILE: Final[str] = "p1-approval-packet.v2.json"
_DEFAULT_PACKET_FILE: Final[str] = "optional3-probe-approval.v2.json"
_LEAF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEAD_SHA = re.compile(r"^[0-9a-f]{40}$")


def main(argv: Sequence[str] | None = None) -> int:
    """`execute`는 local packet, exact evidence, process environment key가 모두 있을 때만 socket을 연다.

    API key, raw provider data, raw query, header, account data를 argv, stdout, receipt에 넣지 않는다.
    Root `.env`를 shell로 source하지도 않으므로 caller는 allowed process environment만 명시적으로 준비해야
    한다.
    """

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] != "execute":
        _emit("OPTIONAL3_PROBE_COMMAND_INVALID", provider_physical_calls=0)
        return 2
    packet_name = _packet_name(arguments[1:])
    if packet_name is None:
        _emit("OPTIONAL3_PROBE_ARGUMENT_INVALID", provider_physical_calls=0)
        return 2
    control_root = _repository_root() / _CONTROL_ROOT_RELATIVE
    now = datetime.now(UTC)
    try:
        packet = Optional3ProbePacket.load_from_control_root(
            control_root=control_root,
            relative_path=packet_name,
            now=now,
        )
    except Optional3ProbeError:
        _emit("OPTIONAL3_PROBE_PACKET_UNAVAILABLE", provider_physical_calls=0)
        return 2
    try:
        binding = _load_execution_binding(
            control_root=control_root,
            repository_root=_repository_root(),
        )
        credential_name = optional3_credential_environment_variable(operation=packet.operation)
        approval = load_and_verify_execution_approval(
            (control_root / _APPROVAL_FILE).absolute(),
            provider_family=packet.provider_family,
            exact_operations=(packet.operation,),
            payload_sha256=packet.packet_sha256(),
            repository_digest=canonical_json_sha256(
                {"headSha": binding.head_sha, "treeSha256": binding.tree_sha256}
            ),
            evidence_digest=canonical_json_sha256(
                {"ciDigest": binding.ci_digest, "securityDigest": binding.security_digest}
            ),
            credential_scope_digest=scope_digest(credential_name),
            physical_call_cap=packet.physical_call_cap,
            cost_cap_microusd=packet.cost_cap_microusd,
            now=now,
        )
        claim_signed_provider_approval(approval)
        api_key = os.environ.get(credential_name, "")
        receipt = Optional3ProbeExecutor(
            control_root=control_root,
            transport=StdlibOptional3ProbeTransport(),
        ).execute(
            packet=packet,
            binding=binding,
            api_key=api_key,
            now=now,
        )
    except (ExecutionApprovalError, ProviderApprovalClaimError):
        _emit("P1_EXECUTION_APPROVAL_REJECTED", provider_physical_calls=0)
        return 2
    except Optional3ProbeError as error:
        _emit(error.code, provider_physical_calls=error.physical_call_count)
        return 2

    _emit(
        "OPTIONAL3_PROBE_EXECUTED",
        provider_physical_calls=receipt.physical_call_count,
        outcome=receipt.outcome,
        provider_family=receipt.provider_family,
        provider_status_class=receipt.provider_status_class,
    )
    return 0 if receipt.outcome == "SUCCESS" else 2


def _packet_name(arguments: tuple[str, ...]) -> str | None:
    if not arguments:
        return _DEFAULT_PACKET_FILE
    if len(arguments) != 2 or arguments[0] != "--packet":
        return None
    value = arguments[1]
    if _LEAF.fullmatch(value) is None:
        return None
    return value


def _load_execution_binding(
    *,
    control_root: Path,
    repository_root: Path,
) -> Optional3ProbeExecutionBinding:
    """Private evidence의 CI/security digest를 clean Git object identity와 independently compare한다."""

    try:
        content = _read_private_control_file(
            root=control_root,
            name=_EVIDENCE_FILE,
            maximum=8 * 1024,
            error_code="OPTIONAL3_PROBE_EXECUTION_EVIDENCE_UNSAFE",
        )
    except Oa112DownloadError as error:
        raise Optional3ProbeError("OPTIONAL3_PROBE_EXECUTION_EVIDENCE_UNAVAILABLE") from error
    document = _parse_canonical_evidence(content)
    binding = Optional3ProbeExecutionBinding(
        ci_digest=_required_hash(document, "ciDigest"),
        head_sha=_required_head_sha(document, "headSha"),
        security_digest=_required_hash(document, "securityDigest"),
        tree_sha256=_required_hash(document, "treeSha256"),
    )
    current_head_sha, current_tree_sha256 = _current_clean_git_identity(repository_root)
    if binding.head_sha != current_head_sha or binding.tree_sha256 != current_tree_sha256:
        raise Optional3ProbeError("OPTIONAL3_PROBE_EXECUTION_EVIDENCE_GIT_DRIFT")
    return binding


def _parse_canonical_evidence(content: bytes) -> Mapping[str, object]:
    if not content or len(content) > 8 * 1024:
        raise Optional3ProbeError("OPTIONAL3_PROBE_EXECUTION_EVIDENCE_INVALID")
    try:
        document = json.loads(content.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Optional3ProbeError("OPTIONAL3_PROBE_EXECUTION_EVIDENCE_INVALID") from error
    if (
        not isinstance(document, Mapping)
        or set(document) != {"ciDigest", "headSha", "securityDigest", "treeSha256"}
        or _canonical_bytes(document) != content
    ):
        raise Optional3ProbeError("OPTIONAL3_PROBE_EXECUTION_EVIDENCE_INVALID")
    return document


def _current_clean_git_identity(repository_root: Path) -> tuple[str, str]:
    """Ignored local credentials/cache는 허용하되 tracked/unignored drift에는 provider socket을 열지 않는다."""

    if not repository_root.is_absolute():
        raise Optional3ProbeError("OPTIONAL3_PROBE_REPOSITORY_INVALID")
    try:
        status = subprocess.run(
            ["git", "-C", str(repository_root), "status", "--porcelain=v1", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if status.stdout:
            raise Optional3ProbeError("OPTIONAL3_PROBE_EXECUTION_EVIDENCE_GIT_DIRTY")
        head = subprocess.run(
            ["git", "-C", str(repository_root), "rev-parse", "--verify", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "-C", str(repository_root), "cat-file", "tree", "HEAD^{tree}"],
            check=True,
            capture_output=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise Optional3ProbeError("OPTIONAL3_PROBE_REPOSITORY_UNAVAILABLE") from error
    if _HEAD_SHA.fullmatch(head) is None or not tree:
        raise Optional3ProbeError("OPTIONAL3_PROBE_REPOSITORY_INVALID")
    return head, hashlib.sha256(tree).hexdigest()


def _required_hash(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise Optional3ProbeError("OPTIONAL3_PROBE_EXECUTION_EVIDENCE_INVALID")
    return value


def _required_head_sha(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or _HEAD_SHA.fullmatch(value) is None:
        raise Optional3ProbeError("OPTIONAL3_PROBE_EXECUTION_EVIDENCE_INVALID")
    return value


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _emit(
    code: object,
    *,
    provider_physical_calls: int,
    outcome: str | None = None,
    provider_family: str | None = None,
    provider_status_class: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "code": code if isinstance(code, str) else "OPTIONAL3_PROBE_UNAVAILABLE",
        "providerPhysicalCalls": provider_physical_calls,
        "state": "COMPLETE" if outcome is not None else "FAILED",
    }
    if outcome is not None:
        payload["outcome"] = outcome
    if provider_family is not None:
        payload["providerFamily"] = provider_family
    if provider_status_class is not None:
        payload["providerStatusClass"] = provider_status_class
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
