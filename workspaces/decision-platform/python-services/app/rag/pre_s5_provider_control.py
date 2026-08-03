"""Pre-S5 provider activation packet의 local-only hard gate다.

S4.5 fixture control-plane과 분리된 이 모듈은 provider socket을 열지 않는다. 신뢰된 local
operator가 만든 0700/0600 packet을 현재 HEAD/tree·CI·security digest에 결속해 읽기만 하며,
key, packet 원문, evidence, nonce는 receipt나 stdout으로 투영하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Mapping

from app.rag.owner_file_io import OwnerFileIoError, read_owner_regular_file

_CONTROL_DIRECTORY = "control"
_VOYAGE_PACKET_FILENAME = "pre-s5-voyage-activation.json"
_VOYAGE_PACKET_RELATIVE_PATH = f"{_CONTROL_DIRECTORY}/{_VOYAGE_PACKET_FILENAME}"
_MAX_PACKET_BYTES = 32 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT = re.compile(r"^[0-9a-f]{40,64}$")
_NONCE = re.compile(r"^ps5_[a-z0-9][a-z0-9_-]{7,123}$")
_OPERATOR = re.compile(r"^[a-z0-9][a-z0-9._@-]{2,127}$")
_PACKET_FIELDS = frozenset(
    {
        "bundleManifestSha256",
        "byteCap",
        "ciDigest",
        "costCapMicrousd",
        "date",
        "endpoint",
        "expiresAt",
        "headCommit",
        "inputMicrousdPerToken",
        "issuedAt",
        "logicalCallCap",
        "nonce",
        "operation",
        "operator",
        "organizationTrainingOptOutEvidenceSha256",
        "origin",
        "paymentMethodPrivacyEvidenceSha256",
        "physicalCallCap",
        "provider",
        "query",
        "rawArtifactCount",
        "rateEvidenceSha256",
        "retryCount",
        "schemaVersion",
        "securityDigest",
        "state",
        "symbol",
        "tokenCap",
        "treeObject",
    }
)


class PreS5ProviderActivationError(ValueError):
    """Pre-S5 provider packet·credential boundary가 fail-closed 했음을 나타낸다."""


@dataclass(frozen=True, slots=True)
class PreS5ProviderBinding:
    """packet이 반드시 일치해야 하는 current tracked-code and gate evidence identity다."""

    head_commit: str
    tree_object: str
    ci_digest: str
    security_digest: str


@dataclass(frozen=True, slots=True)
class PreS5VoyageActivation:
    """outbound 직전 transport가 소비할 최소 Voyage activation projection이다.

    packet 원문의 operator, nonce, evidence hash와 credential은 이 object에 보관하지 않아
    caller가 content-free receipt를 만들 때 capability를 재노출하지 못하게 한다.
    """

    packet_sha256: str
    nonce_sha256: str
    bundle_manifest_sha256: str
    rate_evidence_sha256: str
    provider: str
    operation: str
    origin: str
    endpoint: str
    expires_at: datetime
    logical_call_cap: int
    physical_call_cap: int
    token_cap: int
    byte_cap: int
    cost_cap_microusd: int
    input_microusd_per_token: int
    retry_count: int
    raw_artifact_count: int

    def content_free_summary(self) -> dict[str, object]:
        """CLI/log에 허용되는 activation readiness projection만 반환한다."""

        return {
            "byteCap": self.byte_cap,
            "code": "PRE_S5_VOYAGE_ACTIVATION_READY",
            "costCapMicrousd": self.cost_cap_microusd,
            "expiresAt": _format_instant(self.expires_at),
            "inputMicrousdPerToken": self.input_microusd_per_token,
            "logicalCallCap": self.logical_call_cap,
            "operation": self.operation,
            "packetSha256": self.packet_sha256,
            "physicalCallCap": self.physical_call_cap,
            "provider": self.provider,
            "rawArtifactCount": self.raw_artifact_count,
            "retryCount": self.retry_count,
            "state": "READY",
            "tokenCap": self.token_cap,
        }


def load_pre_s5_voyage_activation(
    *,
    local_root: Path,
    binding: PreS5ProviderBinding,
    now: datetime | None = None,
) -> PreS5VoyageActivation:
    """fixed local packet을 read하고 exact Voyage one-shot authority만 투영한다.

    `local_root`는 CLI argv가 아니라 operator-configured local root여야 한다. 이 함수는
    packet 검증까지만 수행하며, nonce consumption, DB usage reservation, provider transport는
    더 낮은 capability가 별도로 결속하기 전까지 이 entrypoint에서 할 수 없다.
    """

    before = _assert_packet_boundary(local_root)
    try:
        raw = read_owner_regular_file(
            approved_root=local_root,
            relative_path=_VOYAGE_PACKET_RELATIVE_PATH,
            max_bytes=_MAX_PACKET_BYTES,
        ).content
    except OwnerFileIoError as error:
        raise PreS5ProviderActivationError("PRE_S5_PROVIDER_PACKET_BOUNDARY") from error
    after = _assert_packet_boundary(local_root)
    if before != after or len(raw) != before[-1][2]:
        raise PreS5ProviderActivationError("PRE_S5_PROVIDER_PACKET_BOUNDARY")
    try:
        decoded = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PreS5ProviderActivationError("PRE_S5_PROVIDER_PACKET_INVALID") from error
    return _validate_voyage_packet(
        decoded,
        binding=binding,
        now=(now or datetime.now(UTC)).astimezone(UTC),
    )


def resolve_voyage_api_key(environment: Mapping[str, object]) -> str:
    """standard `VOYAGE_API_KEY`만 읽고 legacy variable은 provider credential로 승격하지 않는다."""

    value = environment.get("VOYAGE_API_KEY")
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 4_096
        or value != value.strip()
        or any(character in value for character in ("\x00", "\r", "\n"))
    ):
        raise PreS5ProviderActivationError("PRE_S5_VOYAGE_API_KEY_REQUIRED")
    return value


def _validate_voyage_packet(
    value: object,
    *,
    binding: PreS5ProviderBinding,
    now: datetime,
) -> PreS5VoyageActivation:
    """closed packet shape와 provider/transport boundary를 socket 생성 전에 검증한다."""

    if not isinstance(value, dict) or set(value) != _PACKET_FIELDS:
        raise PreS5ProviderActivationError("PRE_S5_PROVIDER_PACKET_INVALID")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise PreS5ProviderActivationError("PRE_S5_PROVIDER_PACKET_INVALID")
    _validate_binding(binding)
    try:
        issued_at = _parse_instant(value["issuedAt"])
        expires_at = _parse_instant(value["expiresAt"])
    except (KeyError, TypeError, ValueError) as error:
        raise PreS5ProviderActivationError("PRE_S5_PROVIDER_PACKET_INVALID") from error
    if expires_at <= issued_at or expires_at - issued_at > timedelta(minutes=5):
        raise PreS5ProviderActivationError("PRE_S5_PROVIDER_PACKET_INVALID")
    if now >= expires_at:
        raise PreS5ProviderActivationError("PRE_S5_PROVIDER_PACKET_EXPIRED")
    if now < issued_at:
        raise PreS5ProviderActivationError("PRE_S5_PROVIDER_PACKET_INVALID")

    expected_strings = {
        "date": "NONE",
        "endpoint": "/v1/contextualizedembeddings",
        "operation": "CONTEXTUALIZED_DOCUMENT_EMBEDDING",
        "origin": "https://api.voyageai.com",
        "provider": "VOYAGE",
        "query": "FULL_BUNDLE_ORDERED_PRECHUNKED_DOCUMENTS",
        "schemaVersion": "pre-s5-provider-activation/v1",
        "state": "APPROVED",
        "symbol": "NONE",
    }
    if any(value.get(key) != expected for key, expected in expected_strings.items()):
        raise PreS5ProviderActivationError("PRE_S5_PROVIDER_PACKET_INVALID")
    if (
        value.get("headCommit") != binding.head_commit
        or value.get("treeObject") != binding.tree_object
        or value.get("ciDigest") != binding.ci_digest
        or value.get("securityDigest") != binding.security_digest
    ):
        raise PreS5ProviderActivationError("PRE_S5_PROVIDER_PACKET_BINDING")

    hash_fields = (
        "bundleManifestSha256",
        "ciDigest",
        "organizationTrainingOptOutEvidenceSha256",
        "paymentMethodPrivacyEvidenceSha256",
        "rateEvidenceSha256",
        "securityDigest",
    )
    if (
        any(not _is_sha256(value.get(field)) for field in hash_fields)
        or not _GIT_OBJECT.fullmatch(_text(value.get("headCommit")))
        or not _GIT_OBJECT.fullmatch(_text(value.get("treeObject")))
        or not _NONCE.fullmatch(_text(value.get("nonce")))
        or not _OPERATOR.fullmatch(_text(value.get("operator")))
    ):
        raise PreS5ProviderActivationError("PRE_S5_PROVIDER_PACKET_INVALID")

    logical_call_cap = _bounded_int(value.get("logicalCallCap"), minimum=1, maximum=1)
    physical_call_cap = _bounded_int(value.get("physicalCallCap"), minimum=1, maximum=1)
    token_cap = _bounded_int(value.get("tokenCap"), minimum=1, maximum=120_000)
    byte_cap = _bounded_int(value.get("byteCap"), minimum=1, maximum=4_194_304)
    cost_cap_microusd = _bounded_int(value.get("costCapMicrousd"), minimum=1, maximum=1_000_000_000)
    input_microusd_per_token = _bounded_int(
        value.get("inputMicrousdPerToken"),
        minimum=1,
        maximum=1_000_000,
    )
    retry_count = _bounded_int(value.get("retryCount"), minimum=0, maximum=0)
    raw_artifact_count = _bounded_int(value.get("rawArtifactCount"), minimum=0, maximum=0)
    if token_cap * input_microusd_per_token > cost_cap_microusd:
        raise PreS5ProviderActivationError("PRE_S5_PROVIDER_PACKET_INVALID")
    canonical_packet = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return PreS5VoyageActivation(
        packet_sha256=hashlib.sha256(canonical_packet).hexdigest(),
        nonce_sha256=hashlib.sha256(_text(value["nonce"]).encode("utf-8")).hexdigest(),
        bundle_manifest_sha256=_text(value["bundleManifestSha256"]),
        rate_evidence_sha256=_text(value["rateEvidenceSha256"]),
        provider="VOYAGE",
        operation="CONTEXTUALIZED_DOCUMENT_EMBEDDING",
        origin="https://api.voyageai.com",
        endpoint="/v1/contextualizedembeddings",
        expires_at=expires_at,
        logical_call_cap=logical_call_cap,
        physical_call_cap=physical_call_cap,
        token_cap=token_cap,
        byte_cap=byte_cap,
        cost_cap_microusd=cost_cap_microusd,
        input_microusd_per_token=input_microusd_per_token,
        retry_count=retry_count,
        raw_artifact_count=raw_artifact_count,
    )


def _validate_binding(binding: PreS5ProviderBinding) -> None:
    """binding은 packet loader가 만드는 값이 아니므로 ambient/stale digest를 바로 거부한다."""

    if (
        not _GIT_OBJECT.fullmatch(binding.head_commit)
        or not _GIT_OBJECT.fullmatch(binding.tree_object)
        or not _is_sha256(binding.ci_digest)
        or not _is_sha256(binding.security_digest)
    ):
        raise PreS5ProviderActivationError("PRE_S5_PROVIDER_PACKET_INVALID")


def _assert_packet_boundary(local_root: Path) -> tuple[tuple[int, int, int, int, int], ...]:
    """packet read 전후 root/control/file의 POSIX ownership·mode·link identity를 비교한다."""

    if os.name == "nt" or not local_root.is_absolute() or ".." in local_root.parts:
        raise PreS5ProviderActivationError("PRE_S5_PROVIDER_PACKET_BOUNDARY")
    root = _safe_directory_metadata(local_root, expected_mode=0o700)
    control = _safe_directory_metadata(local_root / _CONTROL_DIRECTORY, expected_mode=0o700)
    packet = _safe_file_metadata(
        local_root / _CONTROL_DIRECTORY / _VOYAGE_PACKET_FILENAME,
        expected_mode=0o600,
    )
    return root, control, packet


def _safe_directory_metadata(path: Path, *, expected_mode: int) -> tuple[int, int, int, int, int]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise PreS5ProviderActivationError("PRE_S5_PROVIDER_PACKET_BOUNDARY") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != expected_mode
    ):
        raise PreS5ProviderActivationError("PRE_S5_PROVIDER_PACKET_BOUNDARY")
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _safe_file_metadata(path: Path, *, expected_mode: int) -> tuple[int, int, int, int, int]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise PreS5ProviderActivationError("PRE_S5_PROVIDER_PACKET_BOUNDARY") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != expected_mode
        or not 1 <= metadata.st_size <= _MAX_PACKET_BYTES
    ):
        raise PreS5ProviderActivationError("PRE_S5_PROVIDER_PACKET_BOUNDARY")
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _bounded_int(value: object, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise PreS5ProviderActivationError("PRE_S5_PROVIDER_PACKET_INVALID")
    return value


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _parse_instant(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("instant")
    parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    if parsed.tzinfo is None:
        raise ValueError("instant")
    return parsed.astimezone(UTC)


def _format_instant(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("instant")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
