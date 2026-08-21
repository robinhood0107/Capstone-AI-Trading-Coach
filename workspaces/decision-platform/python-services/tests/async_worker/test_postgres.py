from __future__ import annotations

from dataclasses import replace
import hashlib
import json

import psycopg
import pytest

from app.async_worker.core import AsyncWork, AsyncWorkProcessor
from app.async_worker.postgres import PostgresAsyncWorkRepository


def test_worker_commit_is_atomic_idempotent_and_least_privileged(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    job_id = "job_worker_fixture_00000001"
    event_id = "evt_worker_fixture_00000001"
    payload = {
        "jobId": job_id,
        "ownerRef": "usr_demo_user",
        "artifactId": "artifact_fixture_00000001",
        "contentHash": "sha256:" + "a" * 64,
    }
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    with psycopg.connect(isolated_postgres_cluster["app_dsn"], autocommit=True) as app:
        assert app.execute(
            "SELECT create_async_job(%s,%s,%s,%s::jsonb)",
            (job_id, "ARTIFACT_INGEST", "usr_demo_user", payload_bytes.decode()),
        ).fetchone() == (True,)
        app.execute(
            """
            INSERT INTO event_outbox(
              event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,schema_version
            ) VALUES (%s,%s,'ASYNC_JOB',%s,%s,%s::jsonb,'1.0.0')
            """,
            (
                event_id,
                "artifact.ingest-requested.v1",
                job_id,
                "hmac-sha256:" + "b" * 64,
                payload_bytes.decode(),
            ),
        )
        claimed_event = app.execute(
            "SELECT event_id,claim_token FROM claim_db_async_outbox(%s,100)",
            ("spring-db-dispatcher",),
        ).fetchone()
        assert claimed_event is not None and claimed_event[0] == event_id

    with psycopg.connect(
        isolated_postgres_cluster["worker_dsn"], autocommit=True
    ) as worker:
        claimed_job = worker.execute(
            "SELECT claim_token FROM claim_async_job_by_id(%s,%s)",
            ("spring-db-dispatcher", job_id),
        ).fetchone()
        assert claimed_job is not None
        claim_token = str(claimed_job[0])

    work = AsyncWork(
        event_id=event_id,
        event_type="artifact.ingest-requested.v1",
        schema_version=1,
        payload_hash="sha256:" + hashlib.sha256(payload_bytes).hexdigest(),
        job_id=job_id,
        job_type="ARTIFACT_INGEST",
        payload_json=payload_bytes,
        claim_token=claim_token,
        transport="DB",
    )
    repository = PostgresAsyncWorkRepository(
        isolated_postgres_cluster["worker_dsn"], b"p" * 64
    )
    first = AsyncWorkProcessor(repository).process(work)
    assert first.outcome == "COMPLETED"
    assert AsyncWorkProcessor(repository).process(work).outcome == "DUPLICATE"

    changed_payload = {**payload, "replayOf": "evt_replay_fixture_00000001"}
    changed_bytes = json.dumps(
        changed_payload, sort_keys=True, separators=(",", ":")
    ).encode()
    conflict = AsyncWorkProcessor(repository).process(
        replace(
            work,
            payload_json=changed_bytes,
            payload_hash="sha256:" + hashlib.sha256(changed_bytes).hexdigest(),
        )
    )
    assert conflict.outcome == "NEEDS_REVIEW"
    assert conflict.failure_code == "PAYLOAD_HASH_CONFLICT"

    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as admin:
        assert admin.execute(
            "SELECT status FROM async_job WHERE job_id=%s", (job_id,)
        ).fetchone() == ("COMPLETED",)
        assert admin.execute(
            "SELECT count(*) FROM async_materialization_receipt WHERE job_id=%s", (job_id,)
        ).fetchone() == (1,)
        assert admin.execute(
            "SELECT payload_hash_conflict FROM processed_event WHERE event_id=%s",
            (event_id,),
        ).fetchone() == (True,)
        assert admin.execute(
            "SELECT count(*) FROM event_outbox WHERE aggregate_id=%s", (job_id,)
        ).fetchone() == (2,)

    with psycopg.connect(isolated_postgres_cluster["worker_dsn"]) as worker:
        for table in ("principles", "orders", "users", "flyway_schema_history"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                worker.execute(f"SELECT * FROM {table} LIMIT 1")
            worker.rollback()
