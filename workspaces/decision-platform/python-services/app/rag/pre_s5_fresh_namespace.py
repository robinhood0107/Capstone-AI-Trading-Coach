"""기존 RAG DB/산출물을 재사용하지 않는 Pre-S5 local namespace initializer."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote

_COMPOSE_PROJECT = "capstone-pre-s5-fresh"
_POSTGRES_HOST = "127.0.0.1"
_POSTGRES_HOST_PORT = "55432"
_REDIS_HOST_PORT = "56379"
_MAX_ENV_BYTES = 256 * 1024
_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
_REQUIRED_SECRET_KEYS = frozenset(
    {
        "POSTGRES_ADMIN_PASSWORD",
        "POSTGRES_APP_PASSWORD",
        "POSTGRES_COLLECTOR_PASSWORD",
        "POSTGRES_DB",
        "POSTGRES_DISCLOSURE_READER_PASSWORD",
        "POSTGRES_FILL_WRITER_PASSWORD",
        "POSTGRES_MARKET_WRITER_PASSWORD",
        "POSTGRES_MIGRATION_PASSWORD",
        "POSTGRES_PORTFOLIO_WRITER_PASSWORD",
        "POSTGRES_RAG_ADMIN_PASSWORD",
        "POSTGRES_RAG_QUERY_PASSWORD",
        "POSTGRES_RAG_WRITER_PASSWORD",
        "POSTGRES_RISK_WRITER_PASSWORD",
        "REDIS_PASSWORD",
    }
)


class FreshNamespaceError(ValueError):
    """fresh local root/env boundary가 기존 실행과 섞일 수 있으면 fail-closed한다."""


@dataclass(frozen=True, slots=True)
class FreshNamespacePaths:
    """source secret/corpus와 새 env/output의 명시적 local path 계약이다."""

    source_env: Path
    target_env: Path
    source_root: Path
    output_root: Path


@dataclass(frozen=True, slots=True)
class FreshNamespaceReceipt:
    """secret 값 없이 생성된 namespace 경계만 확인하는 local receipt다."""

    compose_project: str
    postgres_host_port: int
    redis_host_port: int
    source_root: str
    output_root: str
    env_mode: str = "0600"
    directory_mode: str = "0700"
    provider_calls: int = 0
    bge_embedding_inference_calls: int = 0


def initialize_fresh_namespace(paths: FreshNamespacePaths) -> FreshNamespaceReceipt:
    """기존 secret 값만 복제해 고정 포트의 새 env와 빈 output root를 한 번만 만든다.

    source env와 corpus는 읽기만 하며 target env/output이 이미 있거나 link 경계이면 덮어쓰지 않는다.
    """

    _require_absolute_paths(paths)
    _require_private_directory(paths.source_root)
    _require_private_directory(paths.source_env.parent)
    _require_private_directory(paths.target_env.parent)
    if paths.source_root == paths.output_root or paths.source_env == paths.target_env:
        raise FreshNamespaceError("PRE_S5_FRESH_NAMESPACE_BOUNDARY")
    source_values = _read_source_environment(paths.source_env)
    if not _REQUIRED_SECRET_KEYS.issubset(source_values):
        raise FreshNamespaceError("PRE_S5_FRESH_NAMESPACE_SOURCE_ENV")
    _require_absent(paths.target_env)
    _require_absent(paths.output_root)
    if paths.output_root.parent.exists():
        _require_private_directory(paths.output_root.parent)
        created_parent = False
    else:
        if paths.output_root.parent.parent != paths.source_root.parent:
            raise FreshNamespaceError("PRE_S5_FRESH_NAMESPACE_BOUNDARY")
        _require_private_directory(paths.output_root.parent.parent)
        try:
            paths.output_root.parent.mkdir(mode=0o700)
        except OSError:
            raise FreshNamespaceError("PRE_S5_FRESH_NAMESPACE_BOUNDARY") from None
        created_parent = True
    try:
        paths.output_root.mkdir(mode=0o700)
        _require_private_directory(paths.output_root)
        encoded = _fresh_environment(
            source_values,
            source_root=paths.source_root,
            output_root=paths.output_root,
        )
        _write_new_private_file(paths.target_env, encoded)
    except Exception:
        _remove_created_empty_directory(paths.output_root)
        if created_parent:
            _remove_created_empty_directory(paths.output_root.parent)
        raise
    return FreshNamespaceReceipt(
        compose_project=_COMPOSE_PROJECT,
        postgres_host_port=int(_POSTGRES_HOST_PORT),
        redis_host_port=int(_REDIS_HOST_PORT),
        source_root=str(paths.source_root),
        output_root=str(paths.output_root),
    )


def _fresh_environment(
    source: dict[str, str],
    *,
    source_root: Path,
    output_root: Path,
) -> bytes:
    values = {
        key: value
        for key, value in source.items()
        if key
        not in {
            "CAPSTONE_RAG_ADMIN_DATABASE_DSN",
            "CAPSTONE_RAG_LOCAL_ROOT",
            "CAPSTONE_RAG_QUERY_DATABASE_DSN",
            "CAPSTONE_RAG_WRITER_DATABASE_DSN",
            "POSTGRES_HOST",
            "POSTGRES_HOST_PORT",
            "POSTGRES_PORT",
            "RAG_V2_QUERY_DATABASE_DSN",
            "REDIS_HOST_PORT",
        }
    }
    database = source["POSTGRES_DB"]
    values.update(
        {
            "CAPSTONE_PRE_S5_COMPOSE_PROJECT": _COMPOSE_PROJECT,
            "CAPSTONE_RAG_ADMIN_DATABASE_DSN": _dsn(
                "decision_rag_admin", source["POSTGRES_RAG_ADMIN_PASSWORD"], database
            ),
            "CAPSTONE_RAG_OUTPUT_ROOT": str(output_root),
            "CAPSTONE_RAG_QUERY_DATABASE_DSN": _dsn(
                "decision_rag_query", source["POSTGRES_RAG_QUERY_PASSWORD"], database
            ),
            "CAPSTONE_RAG_SOURCE_ROOT": str(source_root),
            "CAPSTONE_RAG_WRITER_DATABASE_DSN": _dsn(
                "decision_rag_writer", source["POSTGRES_RAG_WRITER_PASSWORD"], database
            ),
            "POSTGRES_ADMIN_USER": "postgres",
            "POSTGRES_HOST": _POSTGRES_HOST,
            "POSTGRES_HOST_PORT": _POSTGRES_HOST_PORT,
            "POSTGRES_PORT": "5432",
            "RAG_V2_QUERY_DATABASE_DSN": _dsn(
                "decision_rag_query", source["POSTGRES_RAG_QUERY_PASSWORD"], database
            ),
            "REDIS_HOST_PORT": _REDIS_HOST_PORT,
        }
    )
    encoded = "".join(f"{key}={values[key]}\n" for key in sorted(values)).encode("utf-8")
    if not 1 <= len(encoded) <= _MAX_ENV_BYTES:
        raise FreshNamespaceError("PRE_S5_FRESH_NAMESPACE_SOURCE_ENV")
    return encoded


def _dsn(role: str, password: str, database: str) -> str:
    return (
        f"postgresql://{role}:{quote(password, safe='')}@{_POSTGRES_HOST}:"
        f"{_POSTGRES_HOST_PORT}/{quote(database, safe='')}"
    )


def _read_source_environment(path: Path) -> dict[str, str]:
    raw = _read_private_file(path)
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise FreshNamespaceError("PRE_S5_FRESH_NAMESPACE_SOURCE_ENV") from None
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise FreshNamespaceError("PRE_S5_FRESH_NAMESPACE_SOURCE_ENV")
        key, value = line.split("=", 1)
        if (
            _ENV_KEY.fullmatch(key) is None
            or key in values
            or not value
            or "\0" in value
            or "\r" in value
            or "\n" in value
        ):
            raise FreshNamespaceError("PRE_S5_FRESH_NAMESPACE_SOURCE_ENV")
        values[key] = value
    return values


def _read_private_file(path: Path) -> bytes:
    try:
        metadata = path.lstat()
    except OSError:
        raise FreshNamespaceError("PRE_S5_FRESH_NAMESPACE_BOUNDARY") from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or not 1 <= metadata.st_size <= _MAX_ENV_BYTES
    ):
        raise FreshNamespaceError("PRE_S5_FRESH_NAMESPACE_BOUNDARY")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            opened = os.fstat(descriptor)
            if opened.st_ino != metadata.st_ino or opened.st_dev != metadata.st_dev:
                raise FreshNamespaceError("PRE_S5_FRESH_NAMESPACE_BOUNDARY")
            return os.read(descriptor, _MAX_ENV_BYTES + 1)
        finally:
            os.close(descriptor)
    except OSError:
        raise FreshNamespaceError("PRE_S5_FRESH_NAMESPACE_BOUNDARY") from None


def _write_new_private_file(path: Path, payload: bytes) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("write failed")
            view = view[written:]
        os.fsync(descriptor)
    except OSError:
        raise FreshNamespaceError("PRE_S5_FRESH_NAMESPACE_BOUNDARY") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise FreshNamespaceError("PRE_S5_FRESH_NAMESPACE_BOUNDARY")


def _require_absolute_paths(paths: FreshNamespacePaths) -> None:
    if any(not path.is_absolute() or ".." in path.parts for path in asdict(paths).values()):
        raise FreshNamespaceError("PRE_S5_FRESH_NAMESPACE_BOUNDARY")


def _require_private_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        raise FreshNamespaceError("PRE_S5_FRESH_NAMESPACE_BOUNDARY") from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or path.resolve(strict=True) != path
    ):
        raise FreshNamespaceError("PRE_S5_FRESH_NAMESPACE_BOUNDARY")


def _require_absent(path: Path) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError:
        raise FreshNamespaceError("PRE_S5_FRESH_NAMESPACE_BOUNDARY") from None
    raise FreshNamespaceError("PRE_S5_FRESH_NAMESPACE_BOUNDARY")


def _remove_created_empty_directory(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def main() -> int:
    repository_root = Path(__file__).resolve().parents[5]
    try:
        receipt = initialize_fresh_namespace(
            FreshNamespacePaths(
                source_env=repository_root / "capstone-rag/secrets/docker-compose-rag.env",
                target_env=(
                    repository_root / "capstone-rag/secrets/docker-compose-pre-s5-fresh.env"
                ),
                source_root=repository_root / "capstone-rag/runtime/local-corpus",
                output_root=(repository_root / "capstone-rag/runtime/pre-s5-fresh/local-corpus"),
            )
        )
    except FreshNamespaceError:
        print("PRE_S5_FRESH_NAMESPACE_BOUNDARY", file=sys.stderr)
        return 2
    print(json.dumps(asdict(receipt), separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
