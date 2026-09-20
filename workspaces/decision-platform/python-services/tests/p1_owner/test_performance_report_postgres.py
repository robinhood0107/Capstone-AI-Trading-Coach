from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime

import psycopg

from app.data._shared.canonical_json import canonical_json_bytes
from tests.conftest import PostgresTestCluster


def report(source: str) -> tuple[str, str]:
    payload = {
        "contractId": "owner-performance-report.v1",
        "sourceStart": "2026-08-18",
        "sourceEnd": "2026-09-08",
        "sourceGenerationSha256": source,
        "modelSha256": "b" * 64,
        "principleVersionId": "pvr_" + "c" * 32,
        "principleVersion": 1,
        "costBps": 35,
        "sections": {
            "recalculatedBacktest": {"status": "RECALCULATED"},
            "fixedDailyForecast": {"status": "PARTIAL"},
            "actualTrading": {"status": "NO_REALIZED_TRADES"},
        },
    }
    text = canonical_json_bytes(payload).decode()
    return text, hashlib.sha256(text.encode()).hexdigest()


def test_success_noop_correction_and_failure_preserve_last_success(
    isolated_postgres_cluster: PostgresTestCluster,
) -> None:
    cluster = isolated_postgres_cluster
    now = datetime(2026, 9, 8, 6, 0, tzinfo=UTC)

    def publish(source: str) -> str:
        text, digest = report(source)
        with psycopg.connect(cluster["worker_dsn"]) as db:
            return db.execute(
                "SELECT publish_owner_performance_report_v1(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    "usr_demo_user",
                    "perf_report_" + source[:24],
                    source,
                    date(2026, 8, 18),
                    date(2026, 9, 8),
                    "b" * 64,
                    "pvr_" + "c" * 32,
                    1,
                    35,
                    text,
                    digest,
                    now,
                ),
            ).fetchone()[0]

    assert publish("1" * 64) == "INSERTED"
    assert publish("1" * 64) == "NO_OP"
    assert publish("2" * 64) == "INSERTED"
    with psycopg.connect(cluster["worker_dsn"]) as db:
        assert db.execute(
            "SELECT record_owner_performance_report_failure_v1(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                "usr_demo_user",
                "perf_fail_" + "3" * 24,
                "3" * 64,
                date(2026, 8, 18),
                date(2026, 9, 9),
                "b" * 64,
                "pvr_" + "c" * 32,
                1,
                35,
                "SCENARIO_GUIDE_INFERENCE_INVALID",
                now,
            ),
        ).fetchone() == ("INSERTED",)
    with psycopg.connect(cluster["admin_dsn"]) as db:
        rows = db.execute(
            "SELECT report_version,status,report_json IS NOT NULL,correction_of_report_id,failure_code "
            "FROM owner_performance_report_generations ORDER BY report_version"
        ).fetchall()
    assert rows == [
        (1, "SUCCESS", True, None, None),
        (2, "SUCCESS", True, "perf_report_" + "1" * 24, None),
        (3, "FAILED", False, None, "SCENARIO_GUIDE_INFERENCE_INVALID"),
    ]
