#!/usr/bin/env python3
"""Assemble isolated public-product secrets from a fresh p1ctl init bundle.

The script never prints secret values. Run p1ctl init separately for DEMO and
FULL so their database credentials, actor keys and RAG keys never overlap.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
from pathlib import Path

COMMON_FILES = (
    "postgres.env",
    "redis.env",
    "role-bootstrap.env",
    "migration.env",
    "actor-capability-authority.env",
    "actor-server.p12",
    "actor-client.p12",
    "actor-tls-ca.crt",
    "rag-history-kek-v1.key",
)
FULL_FILES = (
    "seed-import.env",
    "market-data.env",
)
SPRING_KEYS = (
    "POSTGRES_APP_PASSWORD",
    "POSTGRES_WORKER_PASSWORD",
    "POSTGRES_AUTH_PASSWORD",
    "ACTOR_CAPABILITY_SHARED_SECRET",
    "ACTOR_CAPABILITY_PUBLIC_KEY",
    "REDIS_PASSWORD",
    "JWT_SECRET",
    "JWT_ISSUER",
    "JWT_AUDIENCE",
    "LOGIN_SCOPE_HMAC_KEY",
    "PRINCIPLE_CURSOR_HMAC_KEY",
    "DECISION_IDEMPOTENCY_SCOPE_HMAC_KEY",
    "DECISION_GRPC_SHARED_SECRET",
    "BROKERAGE_IDEMPOTENCY_SCOPE_HMAC_KEY",
    "RAG_IDEMPOTENCY_SCOPE_HMAC_KEY",
    "RAG_REQUEST_FINGERPRINT_HMAC_KEY",
    "RAG_PROVIDER_USAGE_HMAC_KEY",
    "RAG_RATE_LIMIT_HMAC_KEY",
    "RAG_HISTORY_CURSOR_HMAC_KEY",
    "ASYNC_CURSOR_HMAC_KEY",
    "ASYNC_PARTITION_HMAC_KEY",
    "ASYNC_WORKER_GRPC_SHARED_SECRET",
    "ACTOR_CAPABILITY_TLS_KEY_STORE_PASSWORD",
)
FULL_FROM_BASE = {
    "BROKERAGE_DB_CAPABILITY_TOKEN_SHA256": "migration.env",
    "BROKERAGE_GRPC_SHARED_SECRET": "spring.env",
    "KIS_MOCK_ORDER_REFERENCE_KEY": "kis-mock.env",
    "RAG_V2_GRPC_SHARED_SECRET": "rag-v2.env",
    "RAG_V2_QUERY_DATABASE_DSN": "rag-v2.env",
    "RAG_V2_VOYAGE_QUERY_WRITER_DSN": "rag-v2.env",
    "RETURN_INFERENCE_GRPC_SHARED_SECRET": "return-inference.env",
    "P1_AUTOMATION_DATABASE_DSN": "automation-runtime.env",
    "AUTOMATION_RUNTIME_SHARED_SECRET": "automation-runtime.env",
}
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROOT_ENV = PROJECT_ROOT / ".env"
VERTEX_ACCOUNT_B64 = "MARS_VERTEX_SERVICE_ACCOUNT_JSON_B64"
EXTERNAL_DEMO = frozenset({"MARS_VERTEX_MODEL_ID", VERTEX_ACCOUNT_B64})
EXTERNAL_FULL = frozenset(
    {
        "MARS_VERTEX_MODEL_ID",
        "MARS_VERTEX_PROJECT_ID",
        VERTEX_ACCOUNT_B64,
        "GOOGLE_OIDC_CLIENT_ID",
        "GOOGLE_OIDC_CLIENT_SECRET",
        "KAKAO_OAUTH_CLIENT_ID",
        "KAKAO_OAUTH_CLIENT_SECRET",
        "VOYAGE_API_KEY",
    }
)
OPENDART_OPERATOR_KEYS = frozenset(
    {
        "OPENDART_API_KEY",
        "OPENDART_DAILY_CALL_LIMIT",
        "OPENDART_DAILY_CALL_BUDGET",
        "OPENDART_MAX_CALLS_PER_RUN",
        "OPENDART_MAX_SYMBOLS_PER_RUN",
    }
)
OPTIONAL_DEMO = frozenset({"MARS_DEMO_PORT"})
OPTIONAL_FULL = (
    frozenset({"GOOGLE_OIDC_ADMIN_SUBJECT_SHA256", "MARS_FULL_PORT"}) | OPENDART_OPERATOR_KEYS
)
KEY_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_./:@+=?%~-]+$")


def checked_file(path: Path, *, private: bool = True) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or linked file: {path.name}")
    if private and path.stat().st_mode & 0o007:
        raise ValueError(f"world-readable file: {path.name}")
    data = path.read_bytes()
    if not data or len(data) > 65536:
        raise ValueError(f"empty or oversized file: {path.name}")
    return data


def env_file(path: Path, *, strict_values: bool = False) -> dict[str, str]:
    raw = checked_file(path)
    result: dict[str, str] = {}
    for line in raw.decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if (
            not separator
            or not KEY_NAME.fullmatch(key)
            or (strict_values and not SAFE_VALUE.fullmatch(value))
            or key in result
        ):
            raise ValueError(f"invalid env line in {path.name}")
        result[key] = value
    return result


def root_operator_values(
    path: Path, allowed: frozenset[str], optional: frozenset[str] = frozenset()
) -> dict[str, str]:
    raw = checked_file(path)
    result: dict[str, str] = {}
    seen: set[str] = set()
    for line in raw.decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if key not in allowed:
            continue
        if (
            not separator
            or key in seen
            or (not value and key not in optional)
            or (value and not SAFE_VALUE.fullmatch(value))
        ):
            raise ValueError("invalid or duplicate allowlisted setting in root .env")
        seen.add(key)
        if value:
            result[key] = value
    return result


def operator_values(path: Path, product: str) -> dict[str, str]:
    required = EXTERNAL_DEMO if product == "demo" else EXTERNAL_FULL
    allowed = required | OPTIONAL_DEMO if product == "demo" else required | OPTIONAL_FULL
    is_root_env = path.name == ".env" and (path.parent / ".git").exists()
    is_root_env = is_root_env or path.resolve() == ROOT_ENV.resolve()
    values = (
        root_operator_values(path, allowed, allowed - required)
        if is_root_env
        else env_file(path, strict_values=True)
    )
    # Optional settings in the checked-in .env.example are intentionally blank.
    # Treat blank optional values as absent so Compose retains its bounded defaults.
    for key in allowed - required:
        if values.get(key) == "":
            values.pop(key)
    if not required.issubset(values) or set(values) - allowed:
        raise ValueError(
            "root .env is missing required product keys or operator env has unexpected keys"
        )
    if any(not SAFE_VALUE.fullmatch(value) for value in values.values()):
        raise ValueError("operator env values must use the supported single-line format")
    port_key = "MARS_DEMO_PORT" if product == "demo" else "MARS_FULL_PORT"
    if port_key in values:
        port = values[port_key]
        if not port.isdecimal() or not 1024 <= int(port) <= 65535:
            raise ValueError("public product host port must be between 1024 and 65535")
    return values


def decode_vertex_service_account_env(external: dict[str, str]) -> tuple[bytes, dict[str, object]]:
    encoded = external[VERTEX_ACCOUNT_B64]
    try:
        payload = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise ValueError("Vertex service-account env value must be canonical base64") from error
    if not payload or len(payload) > 49152 or base64.b64encode(payload).decode("ascii") != encoded:
        raise ValueError("Vertex service-account env value must be canonical base64")
    identity = json.loads(payload)
    if not isinstance(identity, dict) or identity.get("type") != "service_account":
        raise ValueError("Vertex credentials must encode a service account")
    return payload, identity


def write_private(path: Path, data: bytes, mode: int = 0o640) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
    finally:
        os.chmod(path, mode)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", choices=("demo", "full"), required=True)
    parser.add_argument("--base-secrets", type=Path, required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--operator-env", type=Path, default=ROOT_ENV)
    args = parser.parse_args()

    release_dir = args.release_dir.resolve(strict=True)
    if not release_dir.is_dir() or not (release_dir / "mars-images.json").is_file():
        raise ValueError("release directory needs mars-images.json")
    if not (release_dir / f"mars-public-{args.product}.compose.yml").is_file():
        raise ValueError("release directory needs the matching Compose asset")
    base = args.base_secrets.resolve(strict=True)
    if not base.is_dir():
        raise ValueError("base secrets must be a directory")
    if args.operator_env.is_symlink():
        raise ValueError("operator env must not be a symbolic link")
    operator_env = args.operator_env.resolve(strict=True)
    if operator_env.stat().st_mode & 0o077:
        raise ValueError("operator env must be mode 0600")
    external = operator_values(operator_env, args.product)
    vertex_payload, identity = decode_vertex_service_account_env(external)
    vertex_sha256 = hashlib.sha256(vertex_payload).hexdigest()
    if args.product == "full" and identity.get("project_id") != external["MARS_VERTEX_PROJECT_ID"]:
        raise ValueError("Vertex project ID differs from the service account")

    source_files = COMMON_FILES + (FULL_FILES if args.product == "full" else ())
    payloads = {name: checked_file(base / name) for name in source_files}
    if args.product == "full":
        disclosure = base / "disclosure-collector.env"
        if disclosure.exists():
            payloads[disclosure.name] = checked_file(disclosure)
        else:
            # p1ctl init creates the collector role but its optional OpenDART
            # profile file is normally added later by the development runner.
            password = env_file(base / "postgres.env")["POSTGRES_COLLECTOR_PASSWORD"]
            payloads[disclosure.name] = (
                "P1_DISCLOSURE_COLLECTOR_DSN="
                f"postgresql://decision_collector:{password}@postgres:5432/capstone_p1?sslmode=disable\n"
            ).encode("utf-8")
        disclosure_lines = payloads["disclosure-collector.env"].decode("utf-8").splitlines()
        disclosure_lines = [
            line for line in disclosure_lines if not line.startswith("OPENDART_API_KEY=")
        ]
        if "OPENDART_API_KEY" in external:
            disclosure_lines.append(f"OPENDART_API_KEY={external['OPENDART_API_KEY']}")
        payloads["disclosure-collector.env"] = (
            "\n".join(disclosure_lines) + "\n"
        ).encode("utf-8")
    other_product = "full" if args.product == "demo" else "demo"
    other_postgres = release_dir / f"{other_product}-secrets/postgres.env"
    if (
        other_postgres.exists()
        and hashlib.sha256(checked_file(other_postgres)).digest()
        == hashlib.sha256(payloads["postgres.env"]).digest()
    ):
        raise ValueError("DEMO and FULL must use independently generated p1ctl init bundles")
    spring = env_file(base / "spring.env")
    merged = {key: spring[key] for key in SPRING_KEYS}
    merged["STRONG_LLM_GRPC_SHARED_SECRET"] = secrets.token_hex(32)
    merged[VERTEX_ACCOUNT_B64] = external[VERTEX_ACCOUNT_B64]
    if args.product == "full":
        for key, filename in FULL_FROM_BASE.items():
            merged[key] = env_file(base / filename)[key]
        for key in (
            "GOOGLE_OIDC_CLIENT_ID",
            "GOOGLE_OIDC_CLIENT_SECRET",
            "KAKAO_OAUTH_CLIENT_ID",
            "KAKAO_OAUTH_CLIENT_SECRET",
            "VOYAGE_API_KEY",
        ):
            merged[key] = external[key]
        # No person is an admin before the operator verifies a real Google sub.
        merged["GOOGLE_OIDC_ADMIN_SUBJECT_SHA256"] = external.get(
            "GOOGLE_OIDC_ADMIN_SUBJECT_SHA256", secrets.token_hex(32)
        )
        if not re.fullmatch(r"[0-9a-f]{64}", merged["GOOGLE_OIDC_ADMIN_SUBJECT_SHA256"]):
            raise ValueError("admin subject digest must be lowercase SHA-256")
    if any(not SAFE_VALUE.fullmatch(value) for value in merged.values()):
        raise ValueError("a base env value cannot be safely sourced")

    secrets_dir = release_dir / f"{args.product}-secrets"
    compose_env = release_dir / f"{args.product}.env"
    kek_dir = release_dir / "full-kek"
    if (
        secrets_dir.exists()
        or compose_env.exists()
        or (args.product == "full" and kek_dir.exists())
    ):
        raise ValueError("output already exists; never overwrite product secrets")

    staged = Path(tempfile.mkdtemp(prefix=f".{args.product}-secret-stage-", dir=release_dir))
    os.chmod(staged, 0o700)
    try:
        for name, data in payloads.items():
            write_private(staged / name, data)
        lines = "".join(f"{key}={value}\n" for key, value in sorted(merged.items()))
        write_private(staged / f"mars-public-{args.product}.env", lines.encode("utf-8"))
        if args.product == "demo":
            compose_lines = (
                f"MARS_DEMO_SECRET_GID={os.getgid()}\n"
                "MARS_DEMO_SECRETS_DIR=./demo-secrets\n"
                f"MARS_VERTEX_MODEL_ID={external['MARS_VERTEX_MODEL_ID']}\n"
            )
            if "MARS_DEMO_PORT" in external:
                compose_lines += f"MARS_DEMO_PORT={external['MARS_DEMO_PORT']}\n"
        else:
            compose_lines = (
                f"MARS_FULL_SECRET_GID={os.getgid()}\n"
                "MARS_FULL_SECRETS_DIR=./full-secrets\n"
                "MARS_FULL_BROKERAGE_KEK_DIR=./full-kek\n"
                f"MARS_BROKERAGE_DB_CAPABILITY_TOKEN_SHA256={merged['BROKERAGE_DB_CAPABILITY_TOKEN_SHA256']}\n"
                f"MARS_VERTEX_MODEL_ID={external['MARS_VERTEX_MODEL_ID']}\n"
                f"MARS_VERTEX_PROJECT_ID={external['MARS_VERTEX_PROJECT_ID']}\n"
                f"MARS_VERTEX_SERVICE_ACCOUNT_SHA256={vertex_sha256}\n"
            )
            for key in sorted(OPENDART_OPERATOR_KEYS - {"OPENDART_API_KEY"}):
                if key in external:
                    compose_lines += f"{key}={external[key]}\n"
            if "MARS_FULL_PORT" in external:
                compose_lines += f"MARS_FULL_PORT={external['MARS_FULL_PORT']}\n"
        write_private(staged / "compose.env", compose_lines.encode("utf-8"), 0o600)
        if args.product == "full":
            (staged / "kek").mkdir(mode=0o700)
            write_private(staged / "kek/brokerage-kek-v1.key", secrets.token_bytes(32), 0o600)
        os.replace(staged, secrets_dir)
        os.replace(secrets_dir / "compose.env", compose_env)
        if args.product == "full":
            os.replace(secrets_dir / "kek", kek_dir)
    except Exception:
        if staged.exists():
            shutil.rmtree(staged)
        raise

    print(f"MARS_PUBLIC_SECRETS=CREATED product={args.product}")
    if args.product == "full":
        print("MARS_FULL_KEK_OWNER=SET_TO_CONTAINER_UID_65532_BEFORE_START")
        print("MARS_ADMIN=UNCLAIMED_UNTIL_VERIFIED_GOOGLE_SUBJECT")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, UnicodeError, json.JSONDecodeError) as error:
        print(f"MARS_PUBLIC_SECRETS=FAIL reason={type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
