"""검증 완료 Signal bundle을 exact DML 한 transaction으로만 저장하는 S5.5 adapter."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, cast

import psycopg

from app.lightgbm.artifact_ingest import ValidatedSignalBundle, validate_signal_bundle
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.fake_artifacts import signal_row_payload_bytes


ConnectionFactory = Callable[..., AbstractContextManager[psycopg.Connection[Any]]]


@dataclass(frozen=True)
class SignalIngestOutcome:
    """DB exact DML의 INSERTED/REPLAYED 결과와 content-derived signal ID."""

    outcome: str
    signal_id: str


def validate_and_ingest_signal_bundle(
    *,
    approved_root: Path,
    database_dsn: str,
    connection_factory: ConnectionFactory | None = None,
) -> tuple[SignalIngestOutcome, ...]:
    """모든 local file을 먼저 검증한 뒤에만 decision_app transaction을 연다."""

    bundle = validate_signal_bundle(approved_root=approved_root)
    return ingest_validated_signal_bundle(
        bundle,
        database_dsn=database_dsn,
        connection_factory=connection_factory,
    )


def ingest_validated_signal_bundle(
    bundle: ValidatedSignalBundle,
    *,
    database_dsn: str,
    connection_factory: ConnectionFactory | None = None,
) -> tuple[SignalIngestOutcome, ...]:
    """validated immutable projection만 exact function으로 보내고 어느 row 실패든 rollback한다."""

    if not database_dsn.strip() or not bundle.rows:
        raise LightGbmContractError("Signal v2 ingest configuration or rows are missing")
    factory = connection_factory or cast(ConnectionFactory, psycopg.connect)
    try:
        with factory(database_dsn, autocommit=False, connect_timeout=2) as connection:
            connection.execute("SET LOCAL statement_timeout = '5s'")
            connection.execute("SET LOCAL lock_timeout = '2s'")
            actor = connection.execute("SELECT session_user, current_user").fetchone()
            if actor != ("decision_app", "decision_app"):
                raise LightGbmContractError("Signal v2 ingest requires exact decision_app session")
            outcomes = tuple(_ingest_row(connection, bundle, row) for row in bundle.rows)
        return outcomes
    except LightGbmContractError:
        raise
    except (OSError, TimeoutError, psycopg.Error) as error:
        raise LightGbmContractError("Signal v2 exact ingest transaction failed") from error


def _ingest_row(
    connection: psycopg.Connection[Any],
    bundle: ValidatedSignalBundle,
    row: dict[str, object],
) -> SignalIngestOutcome:
    payload_text = signal_row_payload_bytes(row).decode("utf-8")
    result = connection.execute(
        """
        SELECT outcome, signal_id
        FROM ingest_signal_v2_exact(
          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        """,
        (
            "signal-v2-runtime-v1",
            row["producer"],
            row["sourceWorkspace"],
            row["symbol"],
            cast(date, row["sessionDate"]),
            cast(datetime | None, row["asOf"]),
            row["timeframe"],
            row["status"],
            row["reason"],
            row["signal"],
            _decimal_or_none(row["confidence"]),
            _decimal_or_none(row["predictedReturn"]),
            row["evaluationId"],
            row["modelVersion"],
            row["modelReportId"],
            bundle.artifact_sha256,
            row["payloadSha256"],
            row["provenanceSha256"],
            bundle.fixture,
            bundle.provenance_class,
            payload_text,
        ),
    ).fetchone()
    if (
        result is None
        or len(result) != 2
        or result[0] not in {"INSERTED", "REPLAYED"}
        or not isinstance(result[1], str)
        or not result[1].startswith("sigv2_")
    ):
        raise LightGbmContractError("Signal v2 exact DML returned an invalid receipt")
    return SignalIngestOutcome(result[0], result[1])


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))
