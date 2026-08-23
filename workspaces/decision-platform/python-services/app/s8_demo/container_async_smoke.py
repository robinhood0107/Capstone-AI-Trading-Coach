from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict


_MODE = re.compile(r"^(DB|KAFKA)$")


def partition_key(secret: bytes) -> str:
    if len(secret) < 32:
        raise ValueError("P1 smoke partition credential is too short")
    digest = hmac.new(secret, b"s7:ARTIFACT_INGEST:usr_demo_user", hashlib.sha256)
    return "hmac-sha256:" + digest.hexdigest()


def run(database_dsn: str, secret: bytes, mode: str) -> str:
    if _MODE.fullmatch(mode) is None or conninfo_to_dict(database_dsn).get("user") != "decision_demo":
        raise ValueError("P1 smoke execution boundary is invalid")
    key = partition_key(secret)
    with psycopg.connect(database_dsn, autocommit=True, connect_timeout=3) as connection:
        _require_demo_role(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT stage_p1_synthetic_async_request(%s,%s)", (mode, key))
            row = cursor.fetchone()
            if row is None or not isinstance(row[0], str):
                raise RuntimeError("P1 synthetic async request was not staged")
            job_id = row[0]
        for _ in range(90):
            with connection.cursor() as cursor:
                cursor.execute("SELECT verify_p1_synthetic_async_request(%s)", (mode,))
                verified = cursor.fetchone()
            if verified is not None and verified[0] is True:
                return job_id
            time.sleep(2)
    raise TimeoutError("P1 synthetic async request did not complete")


def _require_demo_role(connection: psycopg.Connection[Any]) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user,session_user")
        if cursor.fetchone() != ("decision_demo", "decision_demo"):
            raise ValueError("P1 smoke effective database role mismatch")


def main() -> int:
    try:
        job_id = run(
            os.environ["P1_DEMO_DATABASE_DSN"],
            os.environ["ASYNC_PARTITION_HMAC_KEY"].encode(),
            os.environ["P1_SMOKE_MODE"],
        )
    except (KeyError, OSError, ValueError, RuntimeError, TimeoutError, psycopg.Error):
        print("P1_CONTAINER_ASYNC_E2E=FAILED")
        return 1
    print(f"P1_CONTAINER_ASYNC_E2E=PASS JOB={job_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
