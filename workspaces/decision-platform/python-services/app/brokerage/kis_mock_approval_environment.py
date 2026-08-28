"""KIS_MOCK exact-approval CLI가 사용할 ignored operator `.env` reader."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from app.data._shared.repository_root import repository_root

_REPOSITORY_ROOT = repository_root(__file__, 5)
_OPERATOR_ENV_FILE = _REPOSITORY_ROOT / ".env"
_OPERATOR_ENV_FILE_VARIABLE = "KIS_MOCK_APPROVAL_ENV_FILE"
_MAX_ENV_BYTES = 64 * 1024
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_OPERATOR_VARIABLES = frozenset(
    {
        "KIS_MOCK_BOUND_ACCOUNT_ID",
        "KIS_MOCK_ORDER_REFERENCE_KEY",
        "S3_KIS_MOCK_EXACT_APPROVAL_ID",
        "S3_KIS_MOCK_EXACT_APPROVAL_SHA256",
    }
)


class KISMockApprovalEnvironmentRejected(RuntimeError):
    """private operator latch의 file boundary, mode 또는 requested value가 유효하지 않다."""


def load_kis_mock_approval_environment(*names: str) -> dict[str, str]:
    """exact probe/recovery가 쓸 allowlisted 값만 owner-only root `.env`에서 읽는다.

    이 reader는 process environment를 보거나 변경하지 않는다. approval latch를 일반 서비스
    환경과 분리해 packet 실행권한이 shell injection이나 broad dotenv loading으로 바뀌지 않게 한다.
    """

    requested_names = tuple(names)
    if not requested_names or any(name not in _OPERATOR_VARIABLES for name in requested_names):
        raise KISMockApprovalEnvironmentRejected("operator approval environment is unavailable")
    if len(set(requested_names)) != len(requested_names):
        raise KISMockApprovalEnvironmentRejected("operator approval environment is unavailable")
    values = _read_private_operator_environment()
    selected = {name: values.get(name, "").strip() for name in requested_names}
    if any(not value for value in selected.values()):
        raise KISMockApprovalEnvironmentRejected("operator approval environment is unavailable")
    return selected


def _read_private_operator_environment() -> dict[str, str]:
    """leaf symlink·hardlink·mode drift를 descriptor에서 닫고 bounded UTF-8만 파싱한다."""

    configured = os.environ.get(_OPERATOR_ENV_FILE_VARIABLE, "").strip()
    operator_env_file = Path(configured) if configured else _OPERATOR_ENV_FILE
    if not operator_env_file.is_absolute() or not hasattr(os, "O_NOFOLLOW"):
        raise KISMockApprovalEnvironmentRejected("operator approval environment is unavailable")
    parent_descriptor = _open_no_follow_parent(operator_env_file.parent)
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        name = operator_env_file.name
        if name in {"", ".", ".."} or "/" in name:
            raise KISMockApprovalEnvironmentRejected("operator approval environment is unavailable")
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or metadata.st_size > _MAX_ENV_BYTES
        ):
            raise KISMockApprovalEnvironmentRejected("operator approval environment is unavailable")
        chunks: list[bytes] = []
        remaining = _MAX_ENV_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 16 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        contents = b"".join(chunks)
        if len(contents) > _MAX_ENV_BYTES:
            raise KISMockApprovalEnvironmentRejected("operator approval environment is unavailable")
        return _parse_operator_environment(contents.decode("utf-8"))
    except (OSError, UnicodeDecodeError):
        raise KISMockApprovalEnvironmentRejected(
            "operator approval environment is unavailable"
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_descriptor)


def _open_no_follow_parent(directory: Path) -> int:
    """world-writable repository root도 허용하되 root부터 final parent까지 symlink traversal은 막는다."""

    if not directory.is_absolute() or any(part in {"", ".", ".."} for part in directory.parts):
        raise KISMockApprovalEnvironmentRejected("operator approval environment is unavailable")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open("/", flags)
        for component in directory.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise OSError("operator env parent is not a directory")
        return descriptor
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
        raise KISMockApprovalEnvironmentRejected(
            "operator approval environment is unavailable"
        ) from None


def _parse_operator_environment(contents: str) -> dict[str, str]:
    """unrelated dotenv entries는 무시하되 allowlisted latch의 duplicate/invalid form은 거부한다."""

    values: dict[str, str] = {}
    for source_line in contents.splitlines():
        line = source_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        name, raw_value = line.split("=", maxsplit=1)
        name = name.strip()
        if not _ENVIRONMENT_NAME.fullmatch(name):
            continue
        if name not in _OPERATOR_VARIABLES:
            continue
        if name in values:
            raise KISMockApprovalEnvironmentRejected("operator approval environment is unavailable")
        values[name] = _unquote_value(raw_value.strip())
    return values


def _unquote_value(value: str) -> str:
    """operator 값은 literal로만 읽어 shell expansion·interpolation을 approval input에서 배제한다."""

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
