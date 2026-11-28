#!/usr/bin/env python3
"""Stop private files even when a text scanner cannot read their contents."""

from __future__ import annotations

import argparse
from pathlib import PurePosixPath
import subprocess
import sys

PRIVATE_DIRS = {".work", ".state", ".state-app", ".direnv", "account-data", "order-data", "provider-data"}
PRIVATE_NAMES = {
    "http-client.private.env.json",
    "service-account.json",
    "service-account-key.json",
    "credentials.json",
}
PRIVATE_SUFFIXES = {".p12", ".pfx", ".jks", ".key", ".pem", ".kdbx", ".db", ".sqlite", ".sqlite3"}


def private_path(raw_path: bytes) -> bool:
    path = PurePosixPath(raw_path.decode("utf-8", errors="surrogateescape").lower())
    name = path.name
    return (
        bool(PRIVATE_DIRS.intersection(path.parts[:-1]))
        or any(path.parts[i : i + 2] == ("data", "raw") for i in range(len(path.parts) - 1))
        or name in PRIVATE_NAMES
        or ((name == ".env" or name.startswith(".env.")) and not name.endswith(".example"))
        or path.suffix in PRIVATE_SUFFIXES
    )


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def check(staged: bool, revision_range: str) -> int:
    filter_args = ("--name-only", "-z", "--no-renames", "--diff-filter=ACMTUXB")
    if staged:
        paths = set(git("diff", "--cached", *filter_args, "--").split(b"\0"))
    else:
        # Include merge-only changes and files removed by a later commit in the PR.
        commits = git("rev-list", revision_range, "--").decode("ascii").split()
        paths = {
            path
            for commit in commits
            for path in git("diff-tree", "--root", "-m", "--no-commit-id", "-r", *filter_args, commit, "--").split(b"\0")
        }
    blocked = sum(private_path(path) for path in paths if path)
    if blocked:
        print(f"Publication blocked: {blocked} private path(s). Review the changed files locally.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--range")
    args = parser.parse_args()
    raise SystemExit(check(args.staged, args.range))
