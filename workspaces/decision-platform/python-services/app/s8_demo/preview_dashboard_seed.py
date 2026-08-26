from __future__ import annotations

import argparse
import hashlib
import hmac
import os
from pathlib import Path

import psycopg

from app.s8_demo.synthetic_bundle import build_synthetic_bundle

_EXPECTED_ARTIFACT_ID = "artifact_s8_0ed32aac66088e495ae853bb"
_EXPECTED_CONTENT_HASH = (
    "sha256:0ed32aac66088e495ae853bbac98a35b2c4a22420138bdd58dcdbbb0d9d8ad02"
)
_REQUEST_RUN_ID = "8f6ebc67733ab5b8f7c8dd2ce41ec264"


def seed_preview(*, database_dsn: str, partition_secret: bytes, config_path: Path) -> None:
    if len(partition_secret) < 32:
        raise ValueError("preview_partition_secret_too_short")
    bundle = build_synthetic_bundle(config_path)
    if (
        bundle.artifact_id != _EXPECTED_ARTIFACT_ID
        or bundle.content_hash != _EXPECTED_CONTENT_HASH
    ):
        raise ValueError("preview_bundle_contract_drift")
    partition_key = "hmac-sha256:" + hmac.new(
        partition_secret,
        b"s7:ARTIFACT_INGEST:usr_demo_user",
        hashlib.sha256,
    ).hexdigest()
    with psycopg.connect(database_dsn, autocommit=False, connect_timeout=3) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_user,session_user")
                if cursor.fetchone() != ("decision_demo", "decision_demo"):
                    raise ValueError("preview_database_role_mismatch")
                for kind, file_name, projection, projection_hash in (
                    (
                        "MODEL_EVALUATION",
                        "model-evaluation.json",
                        bundle.model_projection_text,
                        bundle.model_projection_hash,
                    ),
                    (
                        "BACKTEST",
                        "backtest.json",
                        bundle.backtest_projection_text,
                        bundle.backtest_projection_hash,
                    ),
                ):
                    cursor.execute(
                        "SELECT stage_synthetic_dashboard_view(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            bundle.artifact_id,
                            "usr_demo_user",
                            bundle.run_id,
                            file_name,
                            bundle.content_hash,
                            kind,
                            projection,
                            projection_hash,
                            "2026-08-22T00:00:00Z",
                            "2026-09-21T00:00:00Z",
                        ),
                    )
                    cursor.fetchone()
                cursor.execute(
                    "SELECT stage_p1_synthetic_async_request(%s,%s,%s)",
                    ("DB", partition_key, _REQUEST_RUN_ID),
                )
                job = cursor.fetchone()
                if job is None or not isinstance(job[0], str):
                    raise RuntimeError("preview_async_request_not_staged")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage the provider-free P1 dashboard preview")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        seed_preview(
            database_dsn=os.environ["P1_DEMO_DATABASE_DSN"],
            partition_secret=os.environ["ASYNC_PARTITION_HMAC_KEY"].encode(),
            config_path=args.config,
        )
    except (KeyError, OSError, ValueError, RuntimeError, psycopg.Error):
        print("P1_DASHBOARD_PREVIEW_SEED=FAILED")
        return 1
    print("P1_DASHBOARD_PREVIEW_SEED=STAGED_SYNTHETIC_NOT_REAL_TEAM_B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
