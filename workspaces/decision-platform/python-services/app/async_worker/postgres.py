from __future__ import annotations

import hashlib
import hmac
from typing import Any, NoReturn

import psycopg
from psycopg.conninfo import conninfo_to_dict

from app.async_worker.core import (
    AsyncContractError,
    AsyncRetryableError,
    AsyncWork,
    AsyncWorkRepository,
)


class PostgresAsyncWorkRepository(AsyncWorkRepository):
    def __init__(self, database_dsn: str, partition_hmac_key: bytes) -> None:
        if not is_decision_worker_dsn(database_dsn) or len(partition_hmac_key) < 32:
            raise ValueError("async worker requires purpose-scoped DB and partition credentials")
        self._database_dsn = database_dsn
        self._partition_hmac_key = partition_hmac_key

    def claim_job(self, work: AsyncWork, worker_name: str) -> str | None:
        if work.partition_key is None:
            raise AsyncContractError
        try:
            with self._connect(autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT claim_token FROM claim_async_job_by_event(%s,%s,%s,%s,%s,%s)",
                        (worker_name, work.event_id, work.event_type, work.job_id, work.payload_hash, work.partition_key),
                    )
                    row = cursor.fetchone()
                    return None if row is None else str(row[0])
        except psycopg.Error as error:
            _raise_classified(error)

    def record_poison(
        self,
        *,
        event_id: str,
        event_type: str,
        payload_hash: str,
        source_topic: str,
        attempt: int,
        failure_code: str,
    ) -> bool:
        digest = hashlib.sha256(
            f"dlq|{event_id}|{event_type}|{payload_hash}|{failure_code}".encode()
        ).hexdigest()
        try:
            with self._connect(autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT record_kafka_poison(%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            f"evt_dlq_{digest[:32]}",
                            event_id,
                            event_type,
                            payload_hash,
                            source_topic,
                            attempt,
                            failure_code,
                            self._partition_key(f"dlq:{event_id}"),
                        ),
                    )
                    row = cursor.fetchone()
                    return row is not None and bool(row[0])
        except psycopg.Error as error:
            _raise_classified(error)

    def commit(self, work: AsyncWork, result_ref: str) -> str:
        completion_event_id = _completion_event_id(work)
        if work.partition_key is None:
            raise AsyncContractError
        try:
            with self._connect(autocommit=False) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT commit_async_work(
                          %s,%s,%s,%s,%s,%s::uuid,%s,%s,%s
                        )
                        """,
                        (
                            work.event_id,
                            work.event_type,
                            "python-async-worker-v1",
                            work.payload_hash,
                            work.job_id,
                            work.claim_token,
                            result_ref,
                            completion_event_id,
                            work.partition_key,
                        ),
                    )
                    row = cursor.fetchone()
                    if row is None or row[0] not in {"COMPLETED", "DUPLICATE", "PAYLOAD_CONFLICT"}:
                        raise AsyncRetryableError
                connection.commit()
                return str(row[0])
        except psycopg.Error as error:
            _raise_classified(error)

    def fail(self, work: AsyncWork, code: str, error_class: str) -> str:
        if work.claim_token is None:
            return "CONFLICT"
        dlq = self._poison_fields(work, code)
        try:
            with self._connect(autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT fail_async_work(
                          %s,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s
                        )
                        """,
                        (
                            work.job_id,
                            work.claim_token,
                            dlq[0],
                            work.event_id,
                            work.event_type,
                            work.payload_hash,
                            work.source_topic or work.event_type,
                            work.attempt,
                            code,
                            dlq[1],
                        ),
                    )
                    row = cursor.fetchone()
                    return "CONFLICT" if row is None else str(row[0])
        except psycopg.Error:
            return "CONFLICT"

    def quarantine(self, work: AsyncWork, code: str, error_class: str) -> bool:
        dlq = self._poison_fields(work, code)
        if work.claim_token is None:
            return self.record_poison(
                event_id=work.event_id,
                event_type=work.event_type,
                payload_hash=work.payload_hash,
                source_topic=work.source_topic or work.event_type,
                attempt=work.attempt,
                failure_code=code,
            )
        try:
            with self._connect(autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT quarantine_async_work(
                          %s,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s
                        )
                        """,
                        (
                            work.job_id,
                            work.claim_token,
                            dlq[0],
                            work.event_id,
                            work.event_type,
                            work.payload_hash,
                            work.source_topic or work.event_type,
                            work.attempt,
                            code,
                            dlq[1],
                        ),
                    )
                    row = cursor.fetchone()
                    quarantined = row is not None and bool(row[0])
                    if quarantined:
                        return True
            return self.record_poison(
                event_id=work.event_id,
                event_type=work.event_type,
                payload_hash=work.payload_hash,
                source_topic=work.source_topic or work.event_type,
                attempt=work.attempt,
                failure_code=code,
            )
        except psycopg.Error:
            return False

    def _connect(self, *, autocommit: bool) -> psycopg.Connection[Any]:
        connection: psycopg.Connection[Any] = psycopg.connect(
            self._database_dsn,
            autocommit=autocommit,
            connect_timeout=2,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_user, session_user")
                if cursor.fetchone() != ("decision_worker", "decision_worker"):
                    raise ValueError("async worker effective database role mismatch")
        except Exception:
            connection.close()
            raise
        return connection

    def _partition_key(self, job_id: str) -> str:
        digest = hmac.new(self._partition_hmac_key, f"s7:completion:{job_id}".encode(), hashlib.sha256)
        return "hmac-sha256:" + digest.hexdigest()

    def _poison_fields(self, work: AsyncWork, code: str) -> tuple[str, str]:
        digest = hashlib.sha256(
            f"dlq|{work.event_id}|{work.event_type}|{work.payload_hash}|{code}".encode()
        ).hexdigest()
        return f"evt_dlq_{digest[:32]}", self._partition_key(f"dlq:{work.event_id}")


def is_decision_worker_dsn(database_dsn: str) -> bool:
    try:
        return conninfo_to_dict(database_dsn).get("user") == "decision_worker"
    except psycopg.Error:
        return False


def _completion_event_id(work: AsyncWork) -> str:
    digest = hashlib.sha256(f"complete|{work.event_id}|{work.job_id}".encode()).hexdigest()
    return f"evt_completed_{digest[:32]}"


def _raise_classified(error: psycopg.Error) -> NoReturn:
    sqlstate = error.sqlstate
    if sqlstate is None or sqlstate.startswith("08") or sqlstate in {"40001", "40P01", "55P03"}:
        raise AsyncRetryableError from None
    raise AsyncContractError from None
