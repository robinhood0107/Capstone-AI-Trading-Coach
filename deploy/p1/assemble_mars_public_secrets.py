#!/usr/bin/env python3
"""Assemble isolated public-product secrets from a fresh p1ctl init bundle.

The script never prints secret values. Run p1ctl init separately for DEMO and
FULL so their database credentials, actor keys and RAG keys never overlap.
"""

from __future__ import annotations

import argparse
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
    "return-inference.env",
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
    "P1_AUTOMATION_DATABASE_DSN": "automation-runtime.env",
    "AUTOMATION_RUNTIME_SHARED_SECRET": "automation-runtime.env",
}
EXTERNAL_DEMO = frozenset({"MARS_VERTEX_MODEL_ID"})
EXTERNAL_FULL = frozenset(
    {
        "MARS_VERTEX_MODEL_ID",
        "MARS_VERTEX_PROJECT_ID",
        "GOOGLE_OIDC_CLIENT_ID",
        "GOOGLE_OIDC_CLIENT_SECRET",
        "VOYAGE_API_KEY",
    }
)
OPTIONAL_FULL = frozenset({"GOOGLE_OIDC_ADMIN_SUBJECT_SHA256"})
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
    parser.add_argument("--vertex-json", type=Path, required=True)
    parser.add_argument("--operator-env", type=Path, required=True)
    args = parser.parse_args()

    release_dir = args.release_dir.resolve(strict=True)
    if not release_dir.is_dir() or not (release_dir / "mars-images.json").is_file():
        raise ValueError("release directory needs mars-images.json")
    if not (release_dir / f"mars-public-{args.product}.compose.yml").is_file():
        raise ValueError("release directory needs the matching Compose asset")
    base = args.base_secrets.resolve(strict=True)
    if not base.is_dir():
        raise ValueError("base secrets must be a directory")
    external = env_file(args.operator_env, strict_values=True)
    if args.operator_env.stat().st_mode & 0o077:
        raise ValueError("operator env must be mode 0600")
    required = EXTERNAL_DEMO if args.product == "demo" else EXTERNAL_FULL
    allowed = required if args.product == "demo" else required | OPTIONAL_FULL
    if not required.issubset(external) or set(external) - allowed:
        raise ValueError("operator env keys do not match the selected product")
    vertex = checked_file(args.vertex_json)
    if args.vertex_json.stat().st_mode & 0o077:
        raise ValueError("Vertex JSON must be mode 0600")
    identity = json.loads(vertex)
    if not isinstance(identity, dict) or identity.get("type") != "service_account":
        raise ValueError("Vertex JSON must be a service account")
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
    other_product = "full" if args.product == "demo" else "demo"
    other_postgres = release_dir / f"{other_product}-secrets/postgres.env"
    if other_postgres.exists() and hashlib.sha256(checked_file(other_postgres)).digest() == hashlib.sha256(
        payloads["postgres.env"]
    ).digest():
        raise ValueError("DEMO and FULL must use independently generated p1ctl init bundles")
    spring = env_file(base / "spring.env")
    merged = {key: spring[key] for key in SPRING_KEYS}
    merged["STRONG_LLM_GRPC_SHARED_SECRET"] = secrets.token_hex(32)
    if args.product == "full":
        for key, filename in FULL_FROM_BASE.items():
            merged[key] = env_file(base / filename)[key]
        for key in ("GOOGLE_OIDC_CLIENT_ID", "GOOGLE_OIDC_CLIENT_SECRET", "VOYAGE_API_KEY"):
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
    if secrets_dir.exists() or compose_env.exists() or (args.product == "full" and kek_dir.exists()):
        raise ValueError("output already exists; never overwrite product secrets")

    staged = Path(tempfile.mkdtemp(prefix=f".{args.product}-secret-stage-", dir=release_dir))
    os.chmod(staged, 0o700)
    try:
        for name, data in payloads.items():
            write_private(staged / name, data)
        write_private(staged / "vertex-service-account.json", vertex)
        lines = "".join(f"{key}={value}\n" for key, value in sorted(merged.items()))
        write_private(staged / f"mars-public-{args.product}.env", lines.encode("utf-8"))
        if args.product == "demo":
            compose_lines = (
                f"MARS_DEMO_SECRET_GID={os.getgid()}\n"
                "MARS_DEMO_SECRETS_DIR=./demo-secrets\n"
                f"MARS_VERTEX_MODEL_ID={external['MARS_VERTEX_MODEL_ID']}\n"
            )
        else:
            compose_lines = (
                f"MARS_FULL_SECRET_GID={os.getgid()}\n"
                "MARS_FULL_SECRETS_DIR=./full-secrets\n"
                "MARS_FULL_BROKERAGE_KEK_DIR=./full-kek\n"
                f"MARS_BROKERAGE_DB_CAPABILITY_TOKEN_SHA256={merged['BROKERAGE_DB_CAPABILITY_TOKEN_SHA256']}\n"
                f"MARS_VERTEX_MODEL_ID={external['MARS_VERTEX_MODEL_ID']}\n"
                f"MARS_VERTEX_PROJECT_ID={external['MARS_VERTEX_PROJECT_ID']}\n"
            )
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
