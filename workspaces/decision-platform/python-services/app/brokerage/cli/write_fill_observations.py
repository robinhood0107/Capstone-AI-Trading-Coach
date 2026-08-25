"""S3.3 sanitized fill fixture append CLI."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from app.brokerage.fill_observation_writer import (
    append_fill_observation_fixture,
)

_OFFLINE_TARGETS = {"local", "offline", "test", "testcontainers"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True, type=Path)
    arguments = parser.parse_args(argv)
    target = os.environ.get("DECISION_SOURCE_WRITER_OFFLINE_TARGET", "")
    database_dsn = os.environ.get("DECISION_FILL_WRITER_DATABASE_DSN", "")
    if target not in _OFFLINE_TARGETS:
        print(
            "source=fill_observation operation=append code=offline_target_required",
            file=sys.stderr,
        )
        return 2
    try:
        inserted = append_fill_observation_fixture(
            arguments.fixture,
            database_dsn=database_dsn,
        )
    except (OSError, ValueError, PermissionError):
        print(
            "source=fill_observation operation=append code=rejected",
            file=sys.stderr,
        )
        return 1
    print(f"source=fill_observation operation=append code=complete inserted={inserted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
