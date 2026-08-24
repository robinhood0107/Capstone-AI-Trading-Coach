"""Voyage `voyage-context-4`의 local-only official tokenizer boundary다.

Voyage 문서는 model-specific tokenizer를 공개해 실제 API token count를 미리 확인할 수 있다고
명시한다. 이 모듈은 그 artifact를 자동 다운로드하거나 provider API tokenization 호출로 대체하지
않는다. 승인 packet에 pin된 SHA-256과 0700/0600 local artifact만 읽어 preflight token count를
만들며, 파일·hash·parser 어느 하나라도 맞지 않으면 outbound 전에 fail-closed 한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from tokenizers import Tokenizer

_MODEL = "voyage-context-4"
_ARTIFACT_DIRECTORY = "artifacts"
_MODEL_DIRECTORY = _MODEL
_TOKENIZER_FILENAME = "tokenizer.json"
_MAX_TOKENIZER_BYTES = 20 * 1024 * 1024
_MAX_TEXT_BYTES = 64 * 1024
_MAX_BATCH_ITEMS = 16_000
_SHA256_HEX_LENGTH = 64


class PreS5VoyageTokenizerError(ValueError):
    """Official tokenizer artifact 또는 count contract가 안전하게 검증되지 않았음을 나타낸다."""


@runtime_checkable
class PreS5VoyageTokenCounter(Protocol):
    """Transport가 official model tokenizer로만 input token cap을 계산하게 하는 narrow seam이다."""

    @property
    def model(self) -> str:
        """Packet-bound provider model identity를 반환한다."""

    @property
    def tokenizer_sha256(self) -> str:
        """Packet과 비교할 immutable local tokenizer artifact hash를 반환한다."""

    def count_texts(self, *, texts: tuple[str, ...], token_cap: int) -> int:
        """원문을 보존하지 않고 exact local tokenizer count만 반환한다."""


@dataclass(frozen=True, slots=True)
class LocalPreS5VoyageContext4Tokenizer:
    """Hash-pinned `tokenizer.json`을 메모리에서만 쓰는 Voyage token counter다.

    입력은 provider로 보낼 canonical document 또는 query text일 수 있지만 이 object는 count 외
    어떤 projection도 저장하지 않는다. `add_special_tokens=False`는 Voyage의 public tokenizer
    preview contract와 같은 text-token count를 의도하며, provider가 회신한 billed total은 별도
    append-only usage ledger에 그대로 기록된다.
    """

    _tokenizer: Tokenizer
    _tokenizer_sha256: str

    @classmethod
    def from_local_root(
        cls,
        *,
        local_root: Path,
        expected_sha256: str,
    ) -> LocalPreS5VoyageContext4Tokenizer:
        """0700 root 아래 0600 regular artifact를 hash 확인 후 loading한다.

        경로나 URL은 caller가 고를 수 없다. artifact acquisition은 별도 rights/evidence gate의
        책임이며, 이 method는 이미 local에 놓인 exact bytes의 read-only verification만 수행한다.
        """

        if (
            os.name == "nt"
            or not isinstance(local_root, Path)
            or not local_root.is_absolute()
            or ".." in local_root.parts
            or not _is_sha256(expected_sha256)
        ):
            raise PreS5VoyageTokenizerError("PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_BOUNDARY")
        artifact_root = local_root / _ARTIFACT_DIRECTORY
        model_root = artifact_root / _MODEL_DIRECTORY
        tokenizer_path = model_root / _TOKENIZER_FILENAME
        before = (
            _secure_directory(local_root),
            _secure_directory(artifact_root),
            _secure_directory(model_root),
            _secure_file(tokenizer_path),
        )
        raw = _read_exact_regular_file(tokenizer_path, expected_size=before[-1][2])
        after = (
            _secure_directory(local_root),
            _secure_directory(artifact_root),
            _secure_directory(model_root),
            _secure_file(tokenizer_path),
        )
        if before != after or len(raw) != before[-1][2]:
            raise PreS5VoyageTokenizerError("PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_BOUNDARY")
        tokenizer, actual_sha256 = validate_pre_s5_voyage_tokenizer_bytes(raw)
        if actual_sha256 != expected_sha256:
            raise PreS5VoyageTokenizerError("PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_SHA256")
        return cls(_tokenizer=tokenizer, _tokenizer_sha256=actual_sha256)

    @property
    def model(self) -> str:
        """This loader is fixed to the one Voyage profile model used by the RAG contract."""

        return _MODEL

    @property
    def tokenizer_sha256(self) -> str:
        """Return the exact verified file digest without exposing artifact contents."""

        return self._tokenizer_sha256

    def count_texts(self, *, texts: tuple[str, ...], token_cap: int) -> int:
        """Count a bounded ordered request before a lease can be consumed.

        The method does not use a byte/character approximation or network helper. A malformed input,
        a tokenizer failure, or a cap overflow is indistinguishable to the outbound caller and leaves
        its one-shot packet unused.
        """

        if (
            not isinstance(texts, tuple)
            or not 1 <= len(texts) <= _MAX_BATCH_ITEMS
            or type(token_cap) is not int
            or not 1 <= token_cap <= 120_000
            or any(
                not isinstance(text, str)
                or not text.strip()
                or "\x00" in text
                or not 1 <= len(text.encode("utf-8", errors="strict")) <= _MAX_TEXT_BYTES
                for text in texts
            )
        ):
            raise PreS5VoyageTokenizerError("PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_INPUT")
        try:
            encodings = self._tokenizer.encode_batch(list(texts), add_special_tokens=False)
            total = sum(len(encoding.ids) for encoding in encodings)
        except Exception:
            raise PreS5VoyageTokenizerError("PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_INVALID") from None
        if type(total) is not int or not 1 <= total <= token_cap:
            raise PreS5VoyageTokenizerError("PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_CAP")
        return total


def validate_pre_s5_voyage_tokenizer_bytes(raw: bytes) -> tuple[Tokenizer, str]:
    """취득 직후와 runtime load가 동일한 bounded parser로 exact artifact bytes를 검증한다.

    반환값은 process-local tokenizer와 SHA-256뿐이며 원문이나 vocabulary를 receipt/log에
    투영하지 않는다. acquisition은 이 검증이 끝난 뒤에만 fixed local leaf를 publish한다.
    """

    if not isinstance(raw, bytes) or not 1 <= len(raw) <= _MAX_TOKENIZER_BYTES:
        raise PreS5VoyageTokenizerError("PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_INVALID")
    try:
        source = raw.decode("utf-8", errors="strict")
        decoded = json.loads(source)
        _validate_tokenizer_json_shape(decoded)
        tokenizer = Tokenizer.from_str(source)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, Exception) as error:
        # `tokenizers`의 Rust-origin 예외에는 artifact 일부가 포함될 수 있어 detail을 버린다.
        del error
        raise PreS5VoyageTokenizerError("PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_INVALID") from None
    return tokenizer, hashlib.sha256(raw).hexdigest()


def _secure_directory(path: Path) -> tuple[int, int, int, int, int]:
    """Local operator root 안의 directory swap/link escalation을 막는다."""

    try:
        metadata = path.lstat()
    except OSError as error:
        raise PreS5VoyageTokenizerError("PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_BOUNDARY") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise PreS5VoyageTokenizerError("PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_BOUNDARY")
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _secure_file(path: Path) -> tuple[int, int, int, int, int]:
    """Only one 0600 regular local artifact leaf can enter the tokenizer parser."""

    try:
        metadata = path.lstat()
    except OSError as error:
        raise PreS5VoyageTokenizerError("PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_BOUNDARY") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or not 1 <= metadata.st_size <= _MAX_TOKENIZER_BYTES
    ):
        raise PreS5VoyageTokenizerError("PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_BOUNDARY")
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_exact_regular_file(path: Path, *, expected_size: int) -> bytes:
    """TOCTOU 검증 뒤에도 symlink/descriptor swap이 parser에 닿지 않게 raw bytes를 읽는다."""

    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise PreS5VoyageTokenizerError("PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_BOUNDARY") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size != expected_size
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise PreS5VoyageTokenizerError("PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_BOUNDARY")
        chunks = bytearray()
        while len(chunks) <= _MAX_TOKENIZER_BYTES:
            piece = os.read(descriptor, min(1024 * 1024, _MAX_TOKENIZER_BYTES + 1))
            if not piece:
                break
            chunks.extend(piece)
        if len(chunks) != expected_size:
            raise PreS5VoyageTokenizerError("PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_BOUNDARY")
        return bytes(chunks)
    finally:
        os.close(descriptor)


def _validate_tokenizer_json_shape(value: object) -> None:
    """Parser에 과도한 JSON tree를 주입하지 않도록 최소 structural/size boundary를 둔다."""

    if not isinstance(value, dict) or not value or len(value) > 64:
        raise ValueError("tokenizer top-level")
    pending: list[tuple[object, int]] = [(value, 1)]
    nodes = 0
    while pending:
        current, depth = pending.pop()
        nodes += 1
        if nodes > 1_000_000 or depth > 64:
            raise ValueError("tokenizer bounds")
        if isinstance(current, dict):
            if len(current) > 1_000_000 or any(not isinstance(key, str) for key in current):
                raise ValueError("tokenizer object")
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            if len(current) > 1_000_000:
                raise ValueError("tokenizer list")
            pending.extend((item, depth + 1) for item in current)
        elif isinstance(current, str):
            if len(current.encode("utf-8")) > 512 * 1024:
                raise ValueError("tokenizer string")
        elif current is not None and not isinstance(current, (bool, int, float)):
            raise ValueError("tokenizer scalar")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_HEX_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )
