"""Exact approval packet으로만 여는 Voyage official tokenizer 단일 취득 경계다.

이 모듈은 Voyage embedding API를 호출하지 않는다. Voyage AI의 고정 Hugging Face commit에
있는 tokenizer.json 한 파일만 DNS/peer-pinned HTTPS로 한 번 읽고, bounded parser 검증 뒤
ignored 0700/0600 local artifact로 원자 publish한다. 실패 packet은 재사용할 수 없다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from app.rag.bge_acquisition import (
    BgeAcquisitionError,
    _SocketBgeDnsResolver,
    _StdlibBgeHttpsTransport,
    _open_checked_response,
)
from app.rag.owner_file_io import OwnerFileIoError, read_owner_regular_file
from app.rag.oa112_downloader import Oa112DownloadError, load_oa112_execution_binding
from app.rag.pre_s5_provider_control import PreS5ProviderBinding
from app.rag.pre_s5_voyage_tokenizer import (
    PreS5VoyageTokenizerError,
    validate_pre_s5_voyage_tokenizer_bytes,
)

_MODEL = "voyage-context-4"
_REVISION = "8ca946072a18e398cd61f2ad0243b56d0350b1db"
_ORIGIN = "https://huggingface.co"
_ENDPOINT = f"/voyageai/{_MODEL}/raw/{_REVISION}/tokenizer.json"
_URL = f"{_ORIGIN}{_ENDPOINT}"
_PACKET_PATH = "control/pre-s5-voyage-tokenizer-acquisition.json"
_MAX_PACKET_BYTES = 32 * 1024
_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT = re.compile(r"^[0-9a-f]{40,64}$")
_NONCE = re.compile(r"^ps5_[a-z0-9][a-z0-9_-]{7,123}$")
_OPERATOR = re.compile(r"^[a-z0-9][a-z0-9._@-]{2,127}$")
_PACKET_FIELDS = frozenset(
    {
        "byteCap",
        "ciDigest",
        "costCapMicrousd",
        "date",
        "endpoint",
        "expiresAt",
        "headCommit",
        "issuedAt",
        "logicalCallCap",
        "model",
        "nonce",
        "operation",
        "operator",
        "origin",
        "physicalCallCap",
        "provider",
        "query",
        "rawArtifactCount",
        "retryCount",
        "revision",
        "schemaVersion",
        "securityDigest",
        "state",
        "symbol",
        "trackedArtifactCount",
        "treeObject",
    }
)


class PreS5VoyageTokenizerAcquisitionError(ValueError):
    """Packet, transport, artifact, or local publish boundary failure marker."""


class PreS5VoyageTokenizerFetcher(Protocol):
    """검증된 packet 뒤 fixed public artifact를 한 번 읽는 narrow transport seam이다."""

    def fetch(self, *, url: str, byte_cap: int) -> bytes:
        """자동 retry/decompression 없이 bounded raw bytes를 반환한다."""


@dataclass(frozen=True, slots=True)
class PreS5VoyageTokenizerAcquisitionReceipt:
    """원문·nonce 없이 이후 batch packet에 필요한 observed tokenizer identity만 보존한다."""

    packet_sha256: str
    tokenizer_sha256: str
    revision: str
    byte_count: int
    physical_call_count: int = 1

    def content_free_payload(self) -> dict[str, object]:
        return {
            "byteCount": self.byte_count,
            "model": _MODEL,
            "packetSha256": self.packet_sha256,
            "physicalCallCount": self.physical_call_count,
            "rawArtifactCount": 0,
            "revision": self.revision,
            "schemaVersion": 1,
            "state": "ACQUIRED",
            "tokenizerSha256": self.tokenizer_sha256,
            "trackedArtifactCount": 0,
        }


@dataclass(frozen=True, slots=True)
class _Packet:
    packet_sha256: str
    claim_sha256: str
    head_commit: str
    tree_object: str
    ci_digest: str
    security_digest: str
    byte_cap: int
    issued_at: datetime
    expires_at: datetime


class _PinnedHuggingFaceFetcher:
    """기존 검증된 DNS/peer-pinned transport로 fixed immutable URL만 연다."""

    def fetch(self, *, url: str, byte_cap: int) -> bytes:
        if url != _URL or byte_cap != _MAX_ARTIFACT_BYTES:
            raise PreS5VoyageTokenizerAcquisitionError(
                "PRE_S5_VOYAGE_TOKENIZER_DOWNLOAD_SCOPE"
            )
        try:
            with _open_checked_response(
                url,
                resolver=_SocketBgeDnsResolver(),
                transport=_StdlibBgeHttpsTransport(),
            ) as response:
                if response.status_code != 200 or response.headers.get("location"):
                    raise PreS5VoyageTokenizerAcquisitionError(
                        "PRE_S5_VOYAGE_TOKENIZER_DOWNLOAD_STATUS"
                    )
                encoding = response.headers.get("content-encoding", "").strip().lower()
                if encoding not in {"", "identity"}:
                    raise PreS5VoyageTokenizerAcquisitionError(
                        "PRE_S5_VOYAGE_TOKENIZER_DOWNLOAD_ENCODING"
                    )
                content_length = response.headers.get("content-length")
                parsed_length: int | None = None
                if content_length is not None:
                    try:
                        parsed_length = int(content_length, 10)
                    except ValueError:
                        raise PreS5VoyageTokenizerAcquisitionError(
                            "PRE_S5_VOYAGE_TOKENIZER_DOWNLOAD_SIZE"
                        ) from None
                    if not 1 <= parsed_length <= byte_cap:
                        raise PreS5VoyageTokenizerAcquisitionError(
                            "PRE_S5_VOYAGE_TOKENIZER_DOWNLOAD_SIZE"
                        )
                body = bytearray()
                for chunk in response.iter_raw(chunk_size=_CHUNK_BYTES):
                    if not chunk:
                        continue
                    body.extend(chunk)
                    if len(body) > byte_cap:
                        raise PreS5VoyageTokenizerAcquisitionError(
                            "PRE_S5_VOYAGE_TOKENIZER_DOWNLOAD_SIZE"
                        )
                if not body:
                    raise PreS5VoyageTokenizerAcquisitionError(
                        "PRE_S5_VOYAGE_TOKENIZER_DOWNLOAD_SIZE"
                    )
                if parsed_length is not None and len(body) != parsed_length:
                    raise PreS5VoyageTokenizerAcquisitionError(
                        "PRE_S5_VOYAGE_TOKENIZER_DOWNLOAD_SIZE"
                    )
                return bytes(body)
        except PreS5VoyageTokenizerAcquisitionError:
            raise
        except BgeAcquisitionError:
            raise PreS5VoyageTokenizerAcquisitionError(
                "PRE_S5_VOYAGE_TOKENIZER_DOWNLOAD_TRANSPORT"
            ) from None


def acquire_pre_s5_voyage_tokenizer(
    *,
    local_root: Path,
    binding: PreS5ProviderBinding,
    fetcher: PreS5VoyageTokenizerFetcher | None = None,
    now: datetime | None = None,
) -> PreS5VoyageTokenizerAcquisitionReceipt:
    """현재 execution binding의 exact single-use packet을 소비해 local artifact 하나를 publish한다."""

    current = now or datetime.now(tz=UTC)
    packet = _load_packet(local_root=local_root, binding=binding, now=current)
    artifact_root, model_root, artifact_path, receipt_path = _prepare_destination(local_root)
    del artifact_root
    _consume_packet_claim(local_root=local_root, claim_sha256=packet.claim_sha256)
    active_fetcher = fetcher or _PinnedHuggingFaceFetcher()
    try:
        raw = active_fetcher.fetch(url=_URL, byte_cap=packet.byte_cap)
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= packet.byte_cap:
            raise PreS5VoyageTokenizerAcquisitionError(
                "PRE_S5_VOYAGE_TOKENIZER_DOWNLOAD_SIZE"
            )
        try:
            _, tokenizer_sha256 = validate_pre_s5_voyage_tokenizer_bytes(raw)
        except PreS5VoyageTokenizerError:
            raise PreS5VoyageTokenizerAcquisitionError(
                "PRE_S5_VOYAGE_TOKENIZER_ARTIFACT_INVALID"
            ) from None
        receipt = PreS5VoyageTokenizerAcquisitionReceipt(
            packet_sha256=packet.packet_sha256,
            tokenizer_sha256=tokenizer_sha256,
            revision=_REVISION,
            byte_count=len(raw),
        )
        _publish_file(artifact_path, raw)
        try:
            _publish_file(receipt_path, _canonical_json(receipt.content_free_payload()))
        except Exception:
            # receipt 없는 artifact는 downstream에서 승인 identity를 증명할 수 없으므로 제거한다.
            artifact_path.unlink(missing_ok=True)
            _fsync_directory(model_root)
            raise
        return receipt
    except PreS5VoyageTokenizerAcquisitionError:
        raise
    except (OSError, ValueError):
        raise PreS5VoyageTokenizerAcquisitionError(
            "PRE_S5_VOYAGE_TOKENIZER_ACQUISITION_FAILED"
        ) from None


def main(argv: tuple[str, ...] | None = None) -> int:
    """고정 acquire 명령만 받고 credential이나 경로를 argv/stdout에 노출하지 않는다."""

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments != ("acquire",):
        _emit({"code": "PRE_S5_VOYAGE_TOKENIZER_COMMAND_INVALID", "state": "FAILED"})
        return 2
    local_root_value = os.environ.get("CAPSTONE_RAG_LOCAL_ROOT", "").strip()
    local_root = Path(local_root_value)
    if not local_root_value or not local_root.is_absolute() or ".." in local_root.parts:
        _emit({"code": "PRE_S5_VOYAGE_TOKENIZER_LOCAL_ROOT_REQUIRED", "state": "FAILED"})
        return 2
    try:
        execution = load_oa112_execution_binding(
            approved_root=local_root,
            relative_path="pre-s5-voyage-execution-evidence.v1.json",
            repository_root=_repository_root(),
        )
        receipt = acquire_pre_s5_voyage_tokenizer(
            local_root=local_root,
            binding=PreS5ProviderBinding(
                head_commit=execution.head_sha,
                tree_object=execution.tree_sha256,
                ci_digest=execution.ci_digest,
                security_digest=execution.security_digest,
            ),
        )
    except (Oa112DownloadError, PreS5VoyageTokenizerAcquisitionError):
        _emit({"code": "PRE_S5_VOYAGE_TOKENIZER_ACQUISITION_FAILED", "state": "FAILED"})
        return 2
    _emit({"code": "PRE_S5_VOYAGE_TOKENIZER_ACQUIRED", **receipt.content_free_payload()})
    return 0


def _load_packet(
    *, local_root: Path, binding: PreS5ProviderBinding, now: datetime
) -> _Packet:
    _secure_directory(local_root)
    try:
        packet_file = read_owner_regular_file(
            approved_root=local_root,
            relative_path=_PACKET_PATH,
            max_bytes=_MAX_PACKET_BYTES,
        )
        payload = json.loads(
            packet_file.content.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OwnerFileIoError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        raise PreS5VoyageTokenizerAcquisitionError(
            "PRE_S5_VOYAGE_TOKENIZER_PACKET_INVALID"
        ) from None
    if (
        not isinstance(payload, dict)
        or set(payload) != _PACKET_FIELDS
        or packet_file.content != _canonical_json(payload)
    ):
        raise PreS5VoyageTokenizerAcquisitionError("PRE_S5_VOYAGE_TOKENIZER_PACKET_INVALID")
    issued_at = _parse_instant(payload.get("issuedAt"))
    expires_at = _parse_instant(payload.get("expiresAt"))
    hash_fields = (
        payload.get("ciDigest"),
        payload.get("securityDigest"),
    )
    if (
        payload.get("schemaVersion") != 1
        or payload.get("state") != "APPROVED"
        or payload.get("provider") != "HUGGING_FACE_VOYAGEAI"
        or payload.get("operation") != "ACQUIRE_VOYAGE_CONTEXT_4_TOKENIZER"
        or payload.get("origin") != _ORIGIN
        or payload.get("endpoint") != _ENDPOINT
        or payload.get("revision") != _REVISION
        or payload.get("model") != _MODEL
        or payload.get("logicalCallCap") != 1
        or payload.get("physicalCallCap") != 1
        or payload.get("byteCap") != _MAX_ARTIFACT_BYTES
        or payload.get("costCapMicrousd") != 0
        or payload.get("retryCount") != 0
        or payload.get("rawArtifactCount") != 0
        or payload.get("trackedArtifactCount") != 0
        or payload.get("query") != "NONE"
        or payload.get("symbol") != "NONE"
        or payload.get("date") != "NONE"
        or not isinstance(payload.get("headCommit"), str)
        or _GIT_OBJECT.fullmatch(payload["headCommit"]) is None
        or not isinstance(payload.get("treeObject"), str)
        or _GIT_OBJECT.fullmatch(payload["treeObject"]) is None
        or any(not isinstance(value, str) or _SHA256.fullmatch(value) is None for value in hash_fields)
        or not isinstance(payload.get("nonce"), str)
        or _NONCE.fullmatch(payload["nonce"]) is None
        or not isinstance(payload.get("operator"), str)
        or _OPERATOR.fullmatch(payload["operator"]) is None
        or issued_at > now
        or expires_at <= now
        or expires_at - issued_at > timedelta(minutes=5)
    ):
        raise PreS5VoyageTokenizerAcquisitionError("PRE_S5_VOYAGE_TOKENIZER_PACKET_INVALID")
    if (
        payload["headCommit"] != binding.head_commit
        or payload["treeObject"] != binding.tree_object
        or payload["ciDigest"] != binding.ci_digest
        or payload["securityDigest"] != binding.security_digest
    ):
        raise PreS5VoyageTokenizerAcquisitionError("PRE_S5_VOYAGE_TOKENIZER_PACKET_BINDING")
    return _Packet(
        packet_sha256=hashlib.sha256(packet_file.content).hexdigest(),
        claim_sha256=hashlib.sha256(
            f"voyage-tokenizer-nonce\0{payload['nonce']}".encode("utf-8")
        ).hexdigest(),
        head_commit=payload["headCommit"],
        tree_object=payload["treeObject"],
        ci_digest=payload["ciDigest"],
        security_digest=payload["securityDigest"],
        byte_cap=payload["byteCap"],
        issued_at=issued_at,
        expires_at=expires_at,
    )


def _repository_root() -> Path:
    """Execution binding은 installed snapshot이 아니라 현재 primary checkout에 고정한다."""

    for candidate in (Path(__file__).resolve(), *Path(__file__).resolve().parents):
        if (candidate / ".git").exists():
            return candidate
    raise PreS5VoyageTokenizerAcquisitionError(
        "PRE_S5_VOYAGE_TOKENIZER_REPOSITORY_UNAVAILABLE"
    )


def _prepare_destination(local_root: Path) -> tuple[Path, Path, Path, Path]:
    artifact_root = _ensure_directory(local_root / "artifacts")
    model_root = _ensure_directory(artifact_root / _MODEL)
    artifact_path = model_root / "tokenizer.json"
    receipt_path = model_root / "tokenizer.receipt.json"
    if any(path.exists() or path.is_symlink() for path in (artifact_path, receipt_path)):
        raise PreS5VoyageTokenizerAcquisitionError("PRE_S5_VOYAGE_TOKENIZER_ALREADY_PRESENT")
    return artifact_root, model_root, artifact_path, receipt_path


def _consume_packet_claim(*, local_root: Path, claim_sha256: str) -> None:
    claims = _ensure_directory(local_root / "packet-claims")
    scope = _ensure_directory(claims / "voyage-tokenizer")
    claim = scope / claim_sha256
    try:
        descriptor = os.open(
            claim,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
    except FileExistsError:
        raise PreS5VoyageTokenizerAcquisitionError(
            "PRE_S5_VOYAGE_TOKENIZER_PACKET_CONSUMED"
        ) from None
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, b"CONSUMED\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(scope)


def _publish_file(path: Path, content: bytes) -> None:
    temporary = path.parent / f".{path.name}.tmp-{secrets.token_hex(12)}"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("write failed")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        # hard-link publish는 destination이 경합 중 생겨도 overwrite하지 않는다.
        os.link(temporary, path, follow_symlinks=False)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _secure_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        raise PreS5VoyageTokenizerAcquisitionError(
            "PRE_S5_VOYAGE_TOKENIZER_LOCAL_BOUNDARY"
        ) from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise PreS5VoyageTokenizerAcquisitionError(
            "PRE_S5_VOYAGE_TOKENIZER_LOCAL_BOUNDARY"
        )


def _ensure_directory(path: Path) -> Path:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    _secure_directory(path)
    return path


def _parse_instant(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PreS5VoyageTokenizerAcquisitionError("PRE_S5_VOYAGE_TOKENIZER_PACKET_INVALID")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise PreS5VoyageTokenizerAcquisitionError(
            "PRE_S5_VOYAGE_TOKENIZER_PACKET_INVALID"
        ) from None
    if parsed.tzinfo != UTC:
        raise PreS5VoyageTokenizerAcquisitionError("PRE_S5_VOYAGE_TOKENIZER_PACKET_INVALID")
    return parsed


def _reject_duplicate_keys(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"


def _emit(value: Mapping[str, object]) -> None:
    print(json.dumps(value, separators=(",", ":"), sort_keys=True))


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
