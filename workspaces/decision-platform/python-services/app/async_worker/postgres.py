from __future__ import annotations

import hashlib
import hmac
from typing import NoReturn

import psycopg

from app.async_worker.core import (
    AsyncContractError,
    AsyncRetryableError,
    AsyncWork,
    AsyncWorkRepository,
)


class PostgresAsyncWorkRepository(AsyncWorkRepository):
    def __init__(self, database_dsn: str, partition_hmac_key: bytes) -> None:
        if "decision_worker" not in database_dsn or len(partition_hmac_key) < 32:
            raise ValueError("async worker requires purpose-scoped DB and partition credentials")
        self._database_dsn = database_dsn
        self._partition_hmac_key = partition_hmac_key

    def commit(self, work: AsyncWork, result_ref: str) -> str:
        completion_event_id = _completion_event_id(work)
        partition_key = self._partition_key(work.job_id)
        try:
            with psycopg.connect(self._database_dsn, autocommit=False, connect_timeout=2) as connection:
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
                            partition_key,
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
        try:
            with psycopg.connect(self._database_dsn, autocommit=True, connect_timeout=2) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT fail_async_job(%s,%s::uuid,%s,%s)",
                        (work.job_id, work.claim_token, code, error_class),
                    )
                    row = cursor.fetchone()
                    return "CONFLICT" if row is None else str(row[0])
        except psycopg.Error:
            return "CONFLICT"

    def quarantine(self, work: AsyncWork, code: str, error_class: str) -> bool:
        if work.claim_token is None:
            return False
        try:
            with psycopg.connect(self._database_dsn, autocommit=True, connect_timeout=2) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT quarantine_async_job(%s,%s::uuid,%s,%s)",
                        (work.job_id, work.claim_token, code, error_class),
                    )
                    row = cursor.fetchone()
                    return row is not None and bool(row[0])
        except psycopg.Error:
            return False

    def _partition_key(self, job_id: str) -> str:
        digest = hmac.new(self._partition_hmac_key, f"s7:completion:{job_id}".encode(), hashlib.sha256)
        return "hmac-sha256:" + digest.hexdigest()


def _completion_event_id(work: AsyncWork) -> str:
    digest = hashlib.sha256(f"complete|{work.event_id}|{work.job_id}".encode()).hexdigest()
    return f"evt_completed_{digest[:32]}"


def _raise_classified(error: psycopg.Error) -> NoReturn:
    sqlstate = error.sqlstate
    if sqlstate is None or sqlstate.startswith("08") or sqlstate in {"40001", "40P01", "55P03"}:
        raise AsyncRetryableError from None
    raise AsyncContractError from None
