#!/usr/bin/env python3
"""Validate that a KIS mock certification still matches the running source tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, NoReturn, cast


_MAX_FILE_BYTES: Final = 32 * 1024
_HEAD: Final = re.compile(r"^[0-9a-f]{40}$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_BRANCH: Final = re.compile(r"^(?:feature|fix|docs|infra|experiment)/[A-Za-z0-9._/-]{1,120}$")
_TIMESTAMP: Final = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_REQUIRED_CHECKS: Final = frozenset(
    {
        "Contract schema validation",
        "Spring OpenAPI drift",
        "Kotlin ktlint and build",
        "Python quality gates",
        "Repo hygiene",
        "P1 full-app security gates",
    }
)


class MockCertificationGuardError(RuntimeError):
    """The saved certification cannot authorize the current source tree."""


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _read_owner_json(path: Path) -> tuple[dict[str, object], bytes]:
    if not path.is_absolute():
        raise MockCertificationGuardError("KIS_MOCK_CERTIFICATION_FILE_INVALID")
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise MockCertificationGuardError("KIS_MOCK_CERTIFICATION_FILE_UNAVAILABLE") from error
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or not 1 <= info.st_size <= _MAX_FILE_BYTES
        ):
            raise MockCertificationGuardError("KIS_MOCK_CERTIFICATION_FILE_INVALID")
        content = os.read(descriptor, _MAX_FILE_BYTES + 1)
        if len(content) > _MAX_FILE_BYTES or os.read(descriptor, 1):
            raise MockCertificationGuardError("KIS_MOCK_CERTIFICATION_FILE_INVALID")
    finally:
        os.close(descriptor)
    try:
        value: object = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MockCertificationGuardError("KIS_MOCK_CERTIFICATION_FILE_INVALID") from error
    if not isinstance(value, dict) or _canonical_json_bytes(value) != content:
        raise MockCertificationGuardError("KIS_MOCK_CERTIFICATION_FILE_INVALID")
    return cast(dict[str, object], value), content


def _git(repository_root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository_root), *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise MockCertificationGuardError("KIS_MOCK_CERTIFICATION_GIT_INVALID") from error
    return completed.stdout.strip()


def verify_mock_certification(
    repository_root: Path,
    request_path: Path,
    receipt_path: Path,
    *,
    now: datetime | None = None,
) -> None:
    try:
        root_info = repository_root.lstat()
    except OSError as error:
        raise MockCertificationGuardError("KIS_MOCK_CERTIFICATION_REPOSITORY_INVALID") from error
    if not stat.S_ISDIR(root_info.st_mode) or repository_root.is_symlink():
        raise MockCertificationGuardError("KIS_MOCK_CERTIFICATION_REPOSITORY_INVALID")

    request, request_bytes = _read_owner_json(request_path)
    receipt, _ = _read_owner_json(receipt_path)
    expected_request = {
        "branch",
        "commitSha",
        "pullRequest",
        "quantity",
        "requiredChecks",
        "securityEvidenceDigest",
        "symbol",
    }
    checks = request.get("requiredChecks")
    if (
        set(request) != expected_request
        or not isinstance(request.get("branch"), str)
        or _BRANCH.fullmatch(cast(str, request["branch"])) is None
        or not isinstance(request.get("commitSha"), str)
        or _HEAD.fullmatch(cast(str, request["commitSha"])) is None
        or type(request.get("pullRequest")) is not int
        or cast(int, request["pullRequest"]) < 1
        or request.get("symbol") != "005930"
        or request.get("quantity") != 1
        or not isinstance(request.get("securityEvidenceDigest"), str)
        or _SHA256.fullmatch(cast(str, request["securityEvidenceDigest"])) is None
        or not isinstance(checks, list)
        or not all(isinstance(item, str) for item in checks)
        or set(cast(list[str], checks)) != _REQUIRED_CHECKS
        or len(cast(list[str], checks)) != len(_REQUIRED_CHECKS)
    ):
        raise MockCertificationGuardError("KIS_MOCK_CERTIFICATION_REQUEST_INVALID")

    expected_receipt = {"commitSha", "inputSha256", "physicalCalls", "status", "timestamp"}
    expected_calls = (
        {"brokerage": 7, "quote": 1, "token": 0},
        {"brokerage": 7, "quote": 1, "token": 1},
    )
    timestamp = receipt.get("timestamp")
    if (
        set(receipt) != expected_receipt
        or receipt.get("status") != "PASS"
        or receipt.get("commitSha") != request["commitSha"]
        or receipt.get("physicalCalls") not in expected_calls
        or receipt.get("inputSha256") != hashlib.sha256(request_bytes).hexdigest()
        or not isinstance(timestamp, str)
        or _TIMESTAMP.fullmatch(timestamp) is None
    ):
        raise MockCertificationGuardError("KIS_MOCK_CERTIFICATION_RECEIPT_INVALID")
    try:
        certified_at = datetime.fromisoformat(timestamp.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise MockCertificationGuardError("KIS_MOCK_CERTIFICATION_RECEIPT_INVALID") from error
    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    if certified_at > current_time + timedelta(minutes=5):
        raise MockCertificationGuardError("KIS_MOCK_CERTIFICATION_CLOCK_INVALID")

    if _git(repository_root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise MockCertificationGuardError("KIS_MOCK_CERTIFICATION_DIRTY_WORKTREE")
    certified_commit = cast(str, request["commitSha"])
    certified_type = _git(repository_root, "cat-file", "-t", certified_commit)
    if certified_type != "commit":
        raise MockCertificationGuardError("KIS_MOCK_CERTIFICATION_COMMIT_INVALID")
    certified_tree = _git(repository_root, "rev-parse", f"{certified_commit}^{{tree}}")
    current_tree = _git(repository_root, "rev-parse", "HEAD^{tree}")
    if not certified_tree or certified_tree != current_tree:
        raise MockCertificationGuardError("KIS_MOCK_CERTIFICATION_SOURCE_DRIFT")


def _fail(error: Exception) -> NoReturn:
    print(f"CAPSTONE_ERROR={error}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        verify_mock_certification(
            arguments.repository_root.absolute(),
            arguments.request.absolute(),
            arguments.receipt.absolute(),
        )
    except MockCertificationGuardError as error:
        _fail(error)
    print("KIS_MOCK_CERTIFICATION_GUARD=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
