from __future__ import annotations

import argparse
import os
import re
import stat
from pathlib import Path
from typing import Final


class OpenApiEnvironmentError(ValueError):
    """OpenAPI fixture environment가 안전한 단일 해석을 갖지 못할 때 발생한다."""


REQUIRED_NAMES: Final[tuple[str, ...]] = (
    "POSTGRES_DB",
    "POSTGRES_ADMIN_USER",
    "POSTGRES_HOST",
    "POSTGRES_HOST_PORT",
    "POSTGRES_PORT",
    "POSTGRES_ADMIN_PASSWORD",
    "POSTGRES_APP_PASSWORD",
    "POSTGRES_MIGRATION_PASSWORD",
    "POSTGRES_COLLECTOR_PASSWORD",
    "POSTGRES_DISCLOSURE_READER_PASSWORD",
    "POSTGRES_MARKET_WRITER_PASSWORD",
    "POSTGRES_PORTFOLIO_WRITER_PASSWORD",
    "POSTGRES_RISK_WRITER_PASSWORD",
    "DECISION_GRPC_SHARED_SECRET",
    "PYTHON_GRPC_SHARED_SECRET",
    "REDIS_PASSWORD",
    "JWT_SECRET",
    "JWT_ISSUER",
    "JWT_AUDIENCE",
    "LOGIN_SCOPE_HMAC_KEY",
    "PRINCIPLE_CURSOR_HMAC_KEY",
    "DECISION_IDEMPOTENCY_SCOPE_HMAC_KEY",
    "BROKERAGE_IDEMPOTENCY_SCOPE_HMAC_KEY",
    "DEMO_CREDENTIAL_SEPARATION_KEY",
    "DEMO_USER_CREDENTIAL_BUNDLE",
    "DEMO_ADMIN_CREDENTIAL_BUNDLE",
)

_ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*)='([^']*)'$")
_BASE64URL_SECRET = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_BASE64URL_32 = re.compile(r"^[A-Za-z0-9_-]{43}$")
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_BUNDLE = re.compile(
    r"^s21-v1:(usr_demo_user|usr_demo_admin):"
    r"([A-Za-z0-9_-]{43}):"
    r"(\$2[aby]\$12\$[./A-Za-z0-9]{53}):"
    r"([A-Za-z0-9_-]{43})$"
)
_MAX_FILE_BYTES = 32_768


def _read_owned_private_regular_file(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise OpenApiEnvironmentError(
            "OpenAPI environment must be an accessible non-symlink file."
        ) from error

    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OpenApiEnvironmentError("OpenAPI environment must be a regular file.")
        if metadata.st_uid != os.getuid():
            raise OpenApiEnvironmentError("OpenAPI environment must be owned by the current user.")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise OpenApiEnvironmentError("OpenAPI environment permissions must be exactly 0600.")
        if metadata.st_size < 1 or metadata.st_size > _MAX_FILE_BYTES:
            raise OpenApiEnvironmentError("OpenAPI environment has an invalid byte size.")

        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 8192))
            if not chunk:
                raise OpenApiEnvironmentError("OpenAPI environment changed while it was read.")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise OpenApiEnvironmentError("OpenAPI environment changed while it was read.")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _parse_assignments(raw: bytes) -> dict[str, str]:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise OpenApiEnvironmentError("OpenAPI environment must not contain a UTF-8 BOM.")
    if b"\x00" in raw or b"\r" in raw:
        raise OpenApiEnvironmentError("OpenAPI environment must use NUL-free LF text.")
    if not raw.endswith(b"\n"):
        raise OpenApiEnvironmentError("OpenAPI environment must end with one LF.")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise OpenApiEnvironmentError("OpenAPI environment must be valid UTF-8.") from error

    values: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line or line.startswith("#"):
            continue
        match = _ASSIGNMENT.fullmatch(line)
        if match is None:
            raise OpenApiEnvironmentError(
                f"OpenAPI environment line {line_number} must use NAME='VALUE'."
            )
        name, value = match.groups()
        if name not in REQUIRED_NAMES:
            raise OpenApiEnvironmentError(
                f"OpenAPI environment line {line_number} uses an unknown name."
            )
        if name in values:
            raise OpenApiEnvironmentError(
                f"OpenAPI environment line {line_number} duplicates a name."
            )
        values[name] = value

    missing = [name for name in REQUIRED_NAMES if name not in values]
    if missing:
        raise OpenApiEnvironmentError(
            "OpenAPI environment is missing required names: " + ", ".join(missing)
        )
    return values


def _require_fixed_values(values: dict[str, str]) -> None:
    expected = {
        "POSTGRES_DB": "trading",
        "POSTGRES_ADMIN_USER": "postgres",
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_HOST_PORT": "55432",
        "POSTGRES_PORT": "55432",
    }
    for name, exact_value in expected.items():
        if values[name] != exact_value:
            raise OpenApiEnvironmentError(f"{name} must equal the isolated fixture value.")
    if values["POSTGRES_HOST_PORT"] != values["POSTGRES_PORT"]:
        raise OpenApiEnvironmentError("PostgreSQL host and application ports must match.")


def _require_secret_shapes(values: dict[str, str]) -> None:
    general_secret_names = (
        "POSTGRES_ADMIN_PASSWORD",
        "POSTGRES_APP_PASSWORD",
        "POSTGRES_MIGRATION_PASSWORD",
        "POSTGRES_COLLECTOR_PASSWORD",
        "POSTGRES_DISCLOSURE_READER_PASSWORD",
        "POSTGRES_MARKET_WRITER_PASSWORD",
        "POSTGRES_PORTFOLIO_WRITER_PASSWORD",
        "POSTGRES_RISK_WRITER_PASSWORD",
        "DECISION_GRPC_SHARED_SECRET",
        "REDIS_PASSWORD",
        "JWT_SECRET",
        "LOGIN_SCOPE_HMAC_KEY",
        "PRINCIPLE_CURSOR_HMAC_KEY",
        "DECISION_IDEMPOTENCY_SCOPE_HMAC_KEY",
        "BROKERAGE_IDEMPOTENCY_SCOPE_HMAC_KEY",
    )
    for name in general_secret_names:
        if _BASE64URL_SECRET.fullmatch(values[name]) is None:
            raise OpenApiEnvironmentError(f"{name} must use a bounded Base64url-safe value.")
    if len({values[name] for name in general_secret_names}) != len(general_secret_names):
        raise OpenApiEnvironmentError("OpenAPI fixture secrets must not reuse one value.")

    # Spring과 Python gRPC fixture는 shared-secret 계약을 검증하기 위해 같은 값을 쓰되,
    # 그 외 DB/JWT/HMAC secret과는 목적 분리를 유지한다.
    if _BASE64URL_SECRET.fullmatch(values["PYTHON_GRPC_SHARED_SECRET"]) is None:
        raise OpenApiEnvironmentError("PYTHON_GRPC_SHARED_SECRET must use a bounded Base64url-safe value.")
    if values["PYTHON_GRPC_SHARED_SECRET"] != values["DECISION_GRPC_SHARED_SECRET"]:
        raise OpenApiEnvironmentError("Decision and Python gRPC fixture secrets must match.")

    if _BASE64URL_32.fullmatch(values["DEMO_CREDENTIAL_SEPARATION_KEY"]) is None:
        raise OpenApiEnvironmentError(
            "DEMO_CREDENTIAL_SEPARATION_KEY must be canonical 32-byte Base64url."
        )
    if values["DEMO_CREDENTIAL_SEPARATION_KEY"] in {
        values[name] for name in general_secret_names
    }:
        raise OpenApiEnvironmentError(
            "Demo credential separation key must be purpose-separated."
        )

    for name in ("JWT_ISSUER", "JWT_AUDIENCE"):
        if _SAFE_LABEL.fullmatch(values[name]) is None:
            raise OpenApiEnvironmentError(f"{name} has an unsafe or invalid shape.")
    if values["JWT_ISSUER"] == values["JWT_AUDIENCE"]:
        raise OpenApiEnvironmentError("JWT issuer and audience must be distinct.")


def _require_bundle_shapes(values: dict[str, str]) -> None:
    user = _BUNDLE.fullmatch(values["DEMO_USER_CREDENTIAL_BUNDLE"])
    admin = _BUNDLE.fullmatch(values["DEMO_ADMIN_CREDENTIAL_BUNDLE"])
    if user is None or user.group(1) != "usr_demo_user":
        raise OpenApiEnvironmentError("Demo USER credential bundle is malformed.")
    if admin is None or admin.group(1) != "usr_demo_admin":
        raise OpenApiEnvironmentError("Demo ADMIN credential bundle is malformed.")
    if user.group(2) == admin.group(2) or user.group(3) == admin.group(3):
        raise OpenApiEnvironmentError("Demo USER and ADMIN bundles must be separated.")
    if user.group(4) == admin.group(4):
        raise OpenApiEnvironmentError("Demo USER and ADMIN bundle MACs must be distinct.")


def parse_openapi_environment(path: Path) -> dict[str, str]:
    """0600 regular file을 한 descriptor로 읽고 검증된 explicit subprocess env를 반환한다."""

    values = _parse_assignments(_read_owned_private_regular_file(path))
    _require_fixed_values(values)
    _require_secret_shapes(values)
    _require_bundle_shapes(values)
    return {name: values[name] for name in REQUIRED_NAMES}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the isolated S2.1 OpenAPI fixture environment without sourcing it."
    )
    parser.add_argument("path", type=Path)
    arguments = parser.parse_args()
    parse_openapi_environment(arguments.path)
    print("OpenAPI fixture environment validation succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
