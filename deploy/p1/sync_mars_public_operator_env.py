#!/usr/bin/env python3
"""Sync operator-controlled provider settings from the single project-root .env."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import tempfile
from pathlib import Path

from assemble_mars_public_secrets import (
    OPENDART_OPERATOR_KEYS,
    ROOT_ENV,
    SAFE_VALUE,
    VERTEX_ACCOUNT_B64,
    decode_vertex_service_account_env,
    operator_values,
)

KEY_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
ADMIN_SUBJECT_SHA256 = "GOOGLE_OIDC_ADMIN_SUBJECT_SHA256"
PRODUCT_PORT_KEY = {"demo": "MARS_DEMO_PORT", "full": "MARS_FULL_PORT"}
RUNTIME_SECRET_KEYS = frozenset(
    {
        VERTEX_ACCOUNT_B64,
        "GOOGLE_OIDC_CLIENT_ID",
        "GOOGLE_OIDC_CLIENT_SECRET",
        "KAKAO_OAUTH_CLIENT_ID",
        "KAKAO_OAUTH_CLIENT_SECRET",
        "VOYAGE_API_KEY",
        ADMIN_SUBJECT_SHA256,
    }
)


def bundle_lines(path: Path, mode: int) -> tuple[list[str], dict[str, int]]:
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != mode:
        raise ValueError("generated product file has an unexpected permission boundary")
    if path.stat().st_nlink != 1 or path.stat().st_size > 65536:
        raise ValueError("generated product secret file boundary is invalid")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    indexes: dict[str, int] = {}
    for index, line in enumerate(lines):
        item = line.rstrip("\r\n")
        key, separator, value = item.partition("=")
        if not separator or not KEY_NAME.fullmatch(key) or not SAFE_VALUE.fullmatch(value):
            raise ValueError("generated product secret file has an invalid entry")
        if key in indexes:
            raise ValueError("generated product secret file has duplicate entries")
        indexes[key] = index
    return lines, indexes


def atomic_write(path: Path, content: bytes) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".mars-env-sync-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def sync_operator_env(product: str, operator_env: Path, secrets_dir: Path) -> bool:
    external = operator_values(operator_env, product)
    _, identity = decode_vertex_service_account_env(external)
    if product == "full" and identity.get("project_id") != external["MARS_VERTEX_PROJECT_ID"]:
        raise ValueError("Vertex project ID differs from the service account")
    if ADMIN_SUBJECT_SHA256 in external and not re.fullmatch(
        r"[0-9a-f]{64}", external[ADMIN_SUBJECT_SHA256]
    ):
        raise ValueError("admin subject digest must be lowercase SHA-256")

    if secrets_dir.is_symlink() or not secrets_dir.is_dir():
        raise ValueError("product secret directory must be a real directory")
    target = secrets_dir / f"mars-public-{product}.env"
    lines, indexes = bundle_lines(target, 0o640)
    runtime_values = {key: value for key, value in external.items() if key in RUNTIME_SECRET_KEYS}
    if not set(runtime_values).issubset(indexes):
        raise ValueError("generated product secret file is missing an operator setting")

    content_changes: dict[Path, bytes] = {}
    changed = False
    secret_changed = False
    for key, value in runtime_values.items():
        index = indexes[key]
        if lines[index].rstrip("\r\n") != f"{key}={value}":
            lines[index] = f"{key}={value}\n"
            secret_changed = True
    if secret_changed:
        changed = True
        content_changes[target] = "".join(lines).encode("utf-8")

    release_dir = secrets_dir.parent
    compose_env = release_dir / f"{product}.env"
    compose_lines, compose_indexes = bundle_lines(compose_env, 0o600)
    compose_updates = {"MARS_VERTEX_MODEL_ID": external["MARS_VERTEX_MODEL_ID"]}
    port_key = PRODUCT_PORT_KEY[product]
    if port_key in external:
        compose_updates[port_key] = external[port_key]
    if product == "full":
        vertex_payload, _ = decode_vertex_service_account_env(external)
        compose_updates.update(
            {
                "MARS_VERTEX_PROJECT_ID": external["MARS_VERTEX_PROJECT_ID"],
                "MARS_VERTEX_SERVICE_ACCOUNT_SHA256": hashlib.sha256(vertex_payload).hexdigest(),
            }
        )
        compose_updates.update(
            {key: external[key] for key in OPENDART_OPERATOR_KEYS - {"OPENDART_API_KEY"} if key in external}
        )
    compose_changed = False
    for key, value in compose_updates.items():
        if key in compose_indexes:
            index = compose_indexes[key]
            if compose_lines[index].rstrip("\r\n") != f"{key}={value}":
                compose_lines[index] = f"{key}={value}\n"
                compose_changed = True
        else:
            compose_lines.append(f"{key}={value}\n")
            compose_indexes[key] = len(compose_lines) - 1
            compose_changed = True

    optional_compose_keys = (
        (OPENDART_OPERATOR_KEYS - {"OPENDART_API_KEY"}) if product == "full" else frozenset()
    ) | {port_key}
    for key in optional_compose_keys - set(external):
        if key in compose_indexes:
            optional_index = compose_indexes.pop(key)
            compose_lines.pop(optional_index)
            compose_indexes = {
                item_key: item_index - (item_index > optional_index)
                for item_key, item_index in compose_indexes.items()
            }
            compose_changed = True
    if compose_changed:
        changed = True
        content_changes[compose_env] = "".join(compose_lines).encode("utf-8")

    if product == "full":
        disclosure_env = secrets_dir / "disclosure-collector.env"
        disclosure_lines, disclosure_indexes = bundle_lines(disclosure_env, 0o640)
        dart_key = external.get("OPENDART_API_KEY")
        dart_index = disclosure_indexes.get("OPENDART_API_KEY")
        disclosure_changed = False
        if dart_key is None and dart_index is not None:
            disclosure_lines.pop(dart_index)
            disclosure_changed = True
        elif dart_key is not None and dart_index is not None:
            if disclosure_lines[dart_index].rstrip("\r\n") != f"OPENDART_API_KEY={dart_key}":
                disclosure_lines[dart_index] = f"OPENDART_API_KEY={dart_key}\n"
                disclosure_changed = True
        elif dart_key is not None:
            disclosure_lines.append(f"OPENDART_API_KEY={dart_key}\n")
            disclosure_changed = True
        if disclosure_changed:
            changed = True
            content_changes[disclosure_env] = "".join(disclosure_lines).encode("utf-8")

    for path, content in content_changes.items():
        atomic_write(path, content)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", choices=("demo", "full"), required=True)
    parser.add_argument("--operator-env", type=Path, default=ROOT_ENV)
    parser.add_argument("--secrets-dir", type=Path, required=True)
    args = parser.parse_args()
    changed = sync_operator_env(args.product, args.operator_env, args.secrets_dir)
    print(f"MARS_PUBLIC_OPERATOR_ENV_SYNC={'UPDATED' if changed else 'ALREADY_CURRENT'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"MARS_PUBLIC_OPERATOR_ENV_SYNC=FAIL reason={type(error).__name__}")
        raise SystemExit(1) from None
