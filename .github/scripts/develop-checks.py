#!/usr/bin/env python3
"""Select targeted develop checks and require every selected job to succeed."""

import argparse
import subprocess
from pathlib import PurePosixPath

UV_PROJECTS = (
    "capstone-rag/ocr/cpu",
    "capstone-rag/ocr/intel",
    "capstone-rag/ocr/nvidia",
    "workspaces/decision-platform/python-services",
    "workspaces/return-engine",
)
BACKUP_PATHS = {
    "capstone",
    "deploy/p1/backup",
    "deploy/p1/restore-test",
    "deploy/p1/full-appctl",
    "deploy/p1/p1ctl",
    "deploy/p1/release_guard.py",
    "deploy/p1/compose.yml",
    "deploy/p1/compose.db.yml",
    "deploy/p1/compose.offline.db.yml",
    "deploy/p1/docker/postgres-pgvector.Dockerfile",
    "deploy/p1/docker/secret-entrypoint.sh",
    "infra/init/01-extensions.sql",
    "infra/init/02-application-roles.sh",
    "contracts/tests/test_p1_release_guard.py",
    "contracts/tests/test_p1_release_workflow.py",
    ".github/scripts/test-p1-backup.sh",
    ".github/workflows/mars-product-impact.yml",
}


def select_checks(paths: set[str]) -> tuple[list[str], bool]:
    projects = sorted(
        {str(PurePosixPath(path).parent) for path in paths
         if PurePosixPath(path).name in {"uv.lock", "pyproject.toml"}} & set(UV_PROJECTS)
    )
    return projects, bool(paths & BACKUP_PATHS)


def verify(selection: list[str]) -> None:
    for name, required, result in zip(selection[::3], selection[1::3], selection[2::3], strict=True):
        if (required, result) not in {("true", "success"), ("false", "skipped")}:
            raise SystemExit(f"{name}: expected result for required={required!r}, received {result!r}")
        print(f"{name}: {result}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    route = commands.add_parser("route")
    route.add_argument("base")
    route.add_argument("head")
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("checks", nargs="+")
    args = parser.parse_args()
    if args.command == "verify":
        verify(args.checks)
        return
    changed = subprocess.check_output(
        ["git", "diff", "--no-renames", "--name-only", "-z", f"{args.base}...{args.head}", "--"],
        text=True,
    )
    projects, backup = select_checks(set(changed.split("\0")))
    print("locks_required=" + str(bool(projects)).lower())
    print("lock_projects=" + " ".join(projects))
    print("backup_required=" + str(backup).lower())


if __name__ == "__main__":
    main()
