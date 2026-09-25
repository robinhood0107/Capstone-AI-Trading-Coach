#!/usr/bin/env python3
"""Move an existing Vertex service-account JSON into the project-root .env as one Base64 value."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import stat
import tempfile
from pathlib import Path

ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
ENV_KEY = "MARS_VERTEX_SERVICE_ACCOUNT_JSON_B64"
PROJECT_ENV_KEY = "MARS_VERTEX_PROJECT_ID"
MODEL_ENV_KEY = "MARS_VERTEX_MODEL_ID"
MAX_BYTES = 48 * 1024
PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,62}[a-z0-9]$")
MODEL_ID = re.compile(r"^[a-z][a-z0-9.-]{2,127}$")


def private_regular_file(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError("credential input must be a mode-0600 regular file")
    if path.stat().st_nlink != 1:
        raise ValueError("credential input must have one link")
    data = path.read_bytes()
    if not data or len(data) > MAX_BYTES:
        raise ValueError("credential input is empty or oversized")
    return data


def updated_env_bytes(
    existing: bytes, values: dict[str, str], *, replace: bool
) -> tuple[bytes, bool]:
    lines = existing.decode("utf-8").splitlines(keepends=True)
    indexes: dict[str, list[int]] = {key: [] for key in values}
    found_values: dict[str, str] = {}
    for index, line in enumerate(lines):
        key, separator, value = line.rstrip("\r\n").partition("=")
        if separator and key in indexes:
            indexes[key].append(index)
            found_values[key] = value
    if any(len(found) > 1 for found in indexes.values()):
        raise ValueError("root .env contains duplicate Vertex settings")
    changed = False
    for key, value in values.items():
        found = indexes[key]
        replacement = f"{key}={value}\n"
        if found:
            current = found_values[key]
            if current == value:
                continue
            if current and not replace:
                raise ValueError(f"root .env already contains {key}; pass --replace to update")
            lines[found[0]] = replacement
        else:
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += "\n"
            lines.append(replacement)
        changed = True
    return "".join(lines).encode("utf-8"), changed


def read_model_id(path: Path | None) -> str | None:
    if path is None:
        return None
    data = private_regular_file(path)
    matches: list[str] = []
    for line in data.decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator and key == "VERTEX_MODEL_ID":
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            matches.append(value)
    if len(matches) != 1 or not MODEL_ID.fullmatch(matches[0]):
        raise ValueError("legacy model env must contain one valid VERTEX_MODEL_ID")
    return matches[0]


def atomic_write_private(path: Path, content: bytes) -> None:
    file_descriptor, temporary_name = tempfile.mkstemp(prefix=".env.vertex-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(file_descriptor, 0o600)
        with os.fdopen(file_descriptor, "wb") as destination:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "credential_json", type=Path, help="existing mode-0600 service-account JSON"
    )
    parser.add_argument(
        "--env-file", type=Path, default=ROOT_ENV, help="defaults to the project-root .env"
    )
    parser.add_argument(
        "--model-env-file", type=Path, help="optional existing env file to migrate VERTEX_MODEL_ID"
    )
    parser.add_argument(
        "--replace", action="store_true", help="replace existing imported Vertex settings"
    )
    args = parser.parse_args()

    if args.credential_json.is_symlink():
        raise ValueError("credential input must not be a symbolic link")
    credential_path = args.credential_json.resolve(strict=True)
    credential = private_regular_file(credential_path)
    try:
        document = json.loads(credential)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("credential input is not valid JSON") from error
    if (
        not isinstance(document, dict)
        or set(document)
        != {
            "type",
            "project_id",
            "private_key_id",
            "private_key",
            "client_email",
            "client_id",
            "auth_uri",
            "token_uri",
            "auth_provider_x509_cert_url",
            "client_x509_cert_url",
            "universe_domain",
        }
        or document.get("type") != "service_account"
        or not isinstance(document.get("private_key"), str)
        or not isinstance(document.get("client_email"), str)
        or not isinstance(document.get("project_id"), str)
        or not PROJECT_ID.fullmatch(document["project_id"])
        or document.get("token_uri") != "https://oauth2.googleapis.com/token"
        or document.get("universe_domain") != "googleapis.com"
    ):
        raise ValueError("credential input is not a supported Google service account")

    if args.env_file.is_symlink():
        raise ValueError("project-root .env must not be a symbolic link")
    env_file = args.env_file.resolve(strict=True)
    if env_file.name != ".env" or not (env_file.parent / ".git").exists() or not env_file.is_file():
        raise ValueError("operator env must be the project-root .env")
    if stat.S_IMODE(env_file.stat().st_mode) != 0o600:
        raise ValueError("project-root .env must have mode 0600")
    values = {
        ENV_KEY: base64.b64encode(credential).decode("ascii"),
        PROJECT_ENV_KEY: document["project_id"],
    }
    model_id = read_model_id(args.model_env_file)
    if model_id is not None:
        values[MODEL_ENV_KEY] = model_id
    updated, changed = updated_env_bytes(env_file.read_bytes(), values, replace=args.replace)
    if changed:
        atomic_write_private(env_file, updated)
        print("VERTEX_ENV_IMPORT=UPDATED")
    else:
        print("VERTEX_ENV_IMPORT=ALREADY_CURRENT")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"VERTEX_ENV_IMPORT=FAIL reason={type(error).__name__}")
        raise SystemExit(1) from None
