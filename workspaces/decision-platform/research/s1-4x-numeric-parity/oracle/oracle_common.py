"""S1.4X oracle 도구가 공유하는 strict JSON·hash·atomic-file 경계다."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn, TypeGuard


class OracleContractError(ValueError):
    """외부 입력이 동결 contract를 만족하지 않을 때 사용하는 fail-closed 오류다."""


def _fail(message: str) -> NoReturn:
    raise OracleContractError(message)


def _reject_constant(token: str) -> NoReturn:
    _fail(f"non-standard JSON numeric token is forbidden: {token}")


def _parse_integer(token: str) -> int:
    # JSON 자체는 -0을 허용하지만 S1.4X exact-integer wire 계약은 이를 허용하지 않는다.
    if token == "-0":
        _fail("negative-zero integer token is forbidden")
    return int(token)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _validate_decoded_json(value: Any, *, path: str = "$") -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail(f"non-finite JSON number at {path}")
        return
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            _fail(f"unpaired Unicode surrogate at {path}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_decoded_json(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_decoded_json(key, path=f"{path}.<key>")
            _validate_decoded_json(item, path=f"{path}.{key}")


def strict_json_loads(text: str) -> Any:
    """중복 key·비표준 수·integer `-0`을 잃기 전에 거부해 JSON 값을 반환한다."""

    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_int=_parse_integer,
        )
    except OracleContractError:
        raise
    except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
        raise OracleContractError(f"invalid JSON: {exc}") from exc
    _validate_decoded_json(value)
    return value


def strict_json_load(path: Path) -> Any:
    """파일을 strict UTF-8 JSON으로 읽으며 absolute path 내용은 오류에 노출하지 않는다."""

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise OracleContractError(
            f"unable to read JSON file {path.name!r}: {exc.strerror}"
        ) from exc
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise OracleContractError(f"JSON file {path.name!r} is not strict UTF-8") from exc
    return strict_json_loads(text)


def normalize_json_value(value: Any) -> Any:
    """dataclass를 JSON 값으로 바꾸고 모든 깊이의 `-0.0`과 non-finite를 정규화한다."""

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return normalize_json_value(dataclasses.asdict(value))
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("non-finite result value")
        return 0.0 if value == 0.0 else value
    if isinstance(value, bool | int | str) or value is None:
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("JSON object key must be a string")
            normalized[key] = normalize_json_value(item)
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [normalize_json_value(item) for item in value]
    if hasattr(value, "tolist"):
        return normalize_json_value(value.tolist())
    if hasattr(value, "item"):
        return normalize_json_value(value.item())
    _fail(f"value is not JSON serializable: {type(value).__name__}")


def canonical_json_bytes(value: Any, *, trailing_newline: bool = True) -> bytes:
    """BigInt를 보존하는 프로젝트 전용 deterministic JSON bytes를 만든다.

    이 직렬화는 RFC 8785라고 주장하지 않으며 tracked oracle byte hash 생성에만 사용한다.
    """

    normalized = normalize_json_value(value)
    text = json.dumps(
        normalized,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if trailing_newline:
        text += "\n"
    return text.encode("utf-8")


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """같은 directory의 임시 파일을 fsync한 뒤 atomic replace한다."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    """동결 JSON을 deterministic bytes로 만들어 atomic하게 기록한다."""

    atomic_write_bytes(path, canonical_json_bytes(value))


def sha256_bytes(payload: bytes) -> str:
    """메모리 bytes의 lowercase SHA-256을 반환한다."""

    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """큰 파일도 bounded memory로 읽어 lowercase SHA-256을 반환한다."""

    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(chunk_size):
                digest.update(chunk)
    except OSError as exc:
        raise OracleContractError(f"unable to hash file {path.name!r}: {exc.strerror}") from exc
    return digest.hexdigest()


def is_lower_sha256(value: Any) -> TypeGuard[str]:
    """값이 정확한 lowercase SHA-256 문자열인지 판정한다."""

    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def require_lower_sha256(value: Any, *, field: str) -> str:
    """lowercase SHA-256이 아니면 contract 오류로 중단한다."""

    if not is_lower_sha256(value):
        _fail(f"{field} must be a lowercase SHA-256")
    return value


def require_safe_basename(value: Any, *, field: str = "fileName") -> str:
    """binary descriptor가 fixture root 밖을 가리키지 못하게 basename만 허용한다."""

    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or "\x00" in value
        or "/" in value
        or "\\" in value
        or Path(value).name != value
    ):
        _fail(f"{field} must be a safe basename")
    return value


def resolve_within(root: Path, relative: str, *, must_exist: bool = False) -> Path:
    """relative path를 root 안으로만 해석하고 symlink escape를 fail-closed로 막는다."""

    if not relative or "\x00" in relative:
        _fail("relative path is empty or contains NUL")
    candidate_relative = Path(relative)
    if candidate_relative.is_absolute() or ".." in candidate_relative.parts:
        _fail(f"path is not a safe relative path: {relative!r}")
    resolved_root = root.resolve(strict=True)
    try:
        resolved = (resolved_root / candidate_relative).resolve(strict=must_exist)
    except OSError as exc:
        raise OracleContractError(f"path cannot be resolved safely: {relative!r}") from exc
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise OracleContractError(f"path escapes the allowed root: {relative!r}") from exc
    return resolved


def find_repo_root(start: Path | None = None) -> Path:
    """cwd와 무관하게 `.git`과 `AGENTS.md`를 가진 repository root를 찾는다."""

    current = (start or Path(__file__)).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists() and (candidate / "AGENTS.md").is_file():
            return candidate
    _fail("repository root not found")


def sorted_relative_files(
    root: Path,
    include_globs: Iterable[str],
    *,
    exclude_globs: Iterable[str] = (),
) -> list[Path]:
    """glob closure를 POSIX relative-path byte order로 정렬해 반환한다."""

    included: dict[str, Path] = {}
    for pattern in include_globs:
        if not isinstance(pattern, str) or not pattern:
            _fail("includeGlobs entries must be non-empty strings")
        for candidate in root.glob(pattern):
            if candidate.is_file():
                relative = candidate.relative_to(root).as_posix()
                included[relative] = candidate
    excluded: set[str] = set()
    for pattern in exclude_globs:
        if not isinstance(pattern, str) or not pattern:
            _fail("excludeGlobs entries must be non-empty strings")
        excluded.update(
            candidate.relative_to(root).as_posix()
            for candidate in root.glob(pattern)
            if candidate.is_file()
        )
    ordered = sorted(set(included) - excluded, key=lambda item: item.encode())
    return [included[path] for path in ordered]


def canonical_file_manifest(
    root: Path,
    files: Iterable[Path],
) -> tuple[bytes, list[dict[str, str]]]:
    """`<sha><two spaces><relative-path><LF>` closure와 structured entries를 만든다."""

    entries: list[dict[str, str]] = []
    for path in files:
        try:
            relative = path.resolve(strict=True).relative_to(root.resolve(strict=True)).as_posix()
        except (OSError, ValueError) as exc:
            raise OracleContractError("manifest file is outside its declared root") from exc
        entries.append({"path": relative, "sha256": sha256_file(path)})
    entries.sort(key=lambda item: item["path"].encode())
    payload = "".join(
        f"{entry['sha256']}  {entry['path']}\n" for entry in entries
    ).encode("utf-8")
    return payload, entries
