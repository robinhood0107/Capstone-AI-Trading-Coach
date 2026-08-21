"""Transactional append-only PostgreSQL adoption for a verified neutral seed archive."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
from pathlib import Path
from typing import Any, Protocol, cast

import psycopg
from app.data.market_data.archive import (
    MarketDataArchive,
    MarketDataArchiveError,
    read_artifact_table,
    read_market_data_archive,
)
from app.data.market_data.daily_runtime import AcceptedDailyShard
from app.data._shared.canonical_json import canonical_json_sha256


_WRITER_ROLE = "decision_market_writer"
_CALENDAR_REVISION = "XKRX-4.13.2+KIS_CTCA0903R"
_CALENDAR_SHA256 = hashlib.sha256(_CALENDAR_REVISION.encode("ascii")).hexdigest()


class MarketDataRepositoryError(RuntimeError):
    """Stored archive authority, role, or append identity is invalid."""


class CursorLike(Protocol):
    rowcount: int

    def execute(self, query: str, params: tuple[object, ...] = ()) -> Any: ...

    def fetchone(self) -> tuple[object, ...] | None: ...

    def copy(self, statement: str) -> Any: ...


class ConnectionLike(Protocol):
    def cursor(self) -> CursorLike: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SeedAdoptionResult:
    manifest_sha256: str
    outcome: str
    bars: int
    indices: int
    macro: int
    universes: int
    provider_calls: int = 0


@dataclass(frozen=True, slots=True)
class DailyAdoptionResult:
    """Atomic DB adoption outcome for one complete daily shard."""

    manifest_sha256: str
    outcome: str
    bars: int
    indices: int
    macro: int
    universes: int
    provider_calls: int = 0


@dataclass(frozen=True, slots=True)
class AcceptedManifestHead:
    """Latest accepted manifest strictly before a candidate daily session."""

    manifest_sha256: str
    session_date: date


def stage_seed_archive(
    *, database_dsn: str, archive_root: Path, expected_manifest_sha256: str
) -> SeedAdoptionResult:
    """Open the writer DB only after archive verification and exact identity binding."""

    archive = read_market_data_archive(archive_root)
    if archive.manifest_sha256 != expected_manifest_sha256:
        raise MarketDataArchiveError("market-data seed manifest does not match operator binding")
    with psycopg.connect(database_dsn, autocommit=False, connect_timeout=2) as connection:
        return adopt_seed_archive(connection=cast(ConnectionLike, connection), archive=archive)


def adopt_seed_archive(
    *, connection: ConnectionLike, archive: MarketDataArchive
) -> SeedAdoptionResult:
    """Insert manifest first in one transaction; row failure rolls it all back."""

    cursor = connection.cursor()
    try:
        cursor.execute("SELECT session_user, current_user")
        identity = cursor.fetchone()
        if identity != (_WRITER_ROLE, _WRITER_ROLE):
            raise MarketDataRepositoryError("market-data seed DB connection must use writer role")
        last_session = max(artifact.last_session_date for artifact in archive.artifacts)
        cursor.execute(
            """
            INSERT INTO market_data_manifests (
                manifest_sha256, manifest_kind, contract_id, session_date, as_of,
                generation, source_manifest_sha256, previous_manifest_sha256,
                supersedes_sha256, archive_sha256, receipt_set_sha256,
                calendar_revision, calendar_sha256, temporal_quality,
                entitlement_expires_at, status
            ) VALUES (
                %s, 'SEED', 'market-data-seed.v1', %s, %s,
                1, %s, NULL, NULL, %s, NULL,
                %s, %s, 'RECONSTRUCTED_FIXED_LAG', NULL, 'ACCEPTED'
            )
            """,
            (
                archive.manifest_sha256,
                last_session,
                archive.created_at,
                archive.source_manifest_sha256,
                archive.archive_sha256,
                _CALENDAR_REVISION,
                _CALENDAR_SHA256,
            ),
        )
        if cursor.rowcount == 0:
            connection.commit()
            return SeedAdoptionResult(
                manifest_sha256=archive.manifest_sha256,
                outcome="NO_OP",
                bars=0,
                indices=0,
                macro=0,
                universes=0,
            )
        if cursor.rowcount != 1:
            raise MarketDataRepositoryError("market-data manifest insert count is invalid")
        counts = _copy_artifacts(cursor=cursor, archive=archive)
        connection.commit()
        return SeedAdoptionResult(
            manifest_sha256=archive.manifest_sha256,
            outcome="INSERTED",
            bars=counts["BARS"],
            indices=counts["INDICES"],
            macro=counts["MACRO"],
            universes=counts["UNIVERSES"],
        )
    except Exception:
        connection.rollback()
        raise


def stage_daily_shard(
    *, database_dsn: str, accepted: AcceptedDailyShard, expected_manifest_sha256: str
) -> DailyAdoptionResult:
    """Bind the complete shard identity before opening the writer database."""

    if accepted.manifest_sha256 != expected_manifest_sha256:
        raise MarketDataRepositoryError("daily manifest does not match operator binding")
    with psycopg.connect(database_dsn, autocommit=False, connect_timeout=2) as connection:
        return adopt_daily_shard(connection=cast(ConnectionLike, connection), accepted=accepted)


def adopt_daily_shard(
    *, connection: ConnectionLike, accepted: AcceptedDailyShard
) -> DailyAdoptionResult:
    """Insert a complete daily manifest and all normalized rows in one transaction."""

    payload = accepted.payload
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT session_user, current_user")
        identity = cursor.fetchone()
        if identity != (_WRITER_ROLE, _WRITER_ROLE):
            raise MarketDataRepositoryError("market-data daily DB connection must use writer role")
        _require_previous_accepted_head(cursor=cursor, accepted=accepted)
        receipts = cast(list[dict[str, object]], payload["sourceReceipts"])
        receipt_set_sha256 = canonical_json_sha256(receipts)
        cursor.execute(
            """
            INSERT INTO market_data_manifests (
                manifest_sha256, manifest_kind, contract_id, session_date, as_of,
                generation, source_manifest_sha256, previous_manifest_sha256,
                supersedes_sha256, archive_sha256, receipt_set_sha256,
                calendar_revision, calendar_sha256, temporal_quality,
                entitlement_expires_at, status
            ) VALUES (
                %s, 'DAILY', 'market-data-daily-shard.v1', %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, 'RECONSTRUCTED_FIXED_LAG', NULL, 'ACCEPTED'
            )
            """,
            (
                accepted.manifest_sha256,
                payload["sessionDate"],
                payload["asOf"],
                payload["generation"],
                receipt_set_sha256,
                payload["previousAcceptedManifestSha256"],
                payload.get("supersedesSha256"),
                accepted.manifest_sha256,
                receipt_set_sha256,
                cast(dict[str, object], payload["calendar"])["revision"],
                cast(dict[str, object], payload["calendar"])["attestationSha256"],
            ),
        )
        if cursor.rowcount == 0:
            connection.commit()
            return DailyAdoptionResult(
                manifest_sha256=accepted.manifest_sha256,
                outcome="NO_OP",
                bars=0,
                indices=0,
                macro=0,
                universes=0,
            )
        if cursor.rowcount != 1:
            raise MarketDataRepositoryError("daily manifest insert count is invalid")
        counts = _insert_daily_rows(cursor=cursor, accepted=accepted)
        connection.commit()
        return DailyAdoptionResult(
            manifest_sha256=accepted.manifest_sha256,
            outcome="INSERTED",
            bars=counts["bars"],
            indices=counts["indices"],
            macro=counts["macro"],
            universes=counts["universes"],
        )
    except Exception:
        connection.rollback()
        raise


def require_previous_accepted_head(
    *, connection: ConnectionLike, accepted: AcceptedDailyShard
) -> AcceptedManifestHead:
    """Read and bind the authoritative predecessor without mutating stored state."""

    cursor = connection.cursor()
    cursor.execute("SELECT session_user, current_user")
    identity = cursor.fetchone()
    if identity != (_WRITER_ROLE, _WRITER_ROLE):
        raise MarketDataRepositoryError("market-data daily DB connection must use writer role")
    return _require_previous_accepted_head(cursor=cursor, accepted=accepted)


def _require_previous_accepted_head(
    *, cursor: CursorLike, accepted: AcceptedDailyShard
) -> AcceptedManifestHead:
    payload = accepted.payload
    cursor.execute(
        """
        SELECT manifest_sha256, session_date
        FROM current_market_data_manifest_head(%s)
        """,
        (payload["sessionDate"],),
    )
    row = cursor.fetchone()
    expected = payload["previousAcceptedManifestSha256"]
    if row is None or row[0] != expected:
        raise MarketDataRepositoryError(
            "NEEDS_HUMAN: previous accepted market-data manifest is not the DB head"
        )
    session_date = row[1]
    if not isinstance(session_date, date):
        raise MarketDataRepositoryError("market-data accepted head session is invalid")
    return AcceptedManifestHead(manifest_sha256=cast(str, row[0]), session_date=session_date)


def _insert_daily_rows(
    *, cursor: CursorLike, accepted: AcceptedDailyShard
) -> dict[str, int]:
    payload = accepted.payload
    generation = payload["generation"]
    for row in cast(list[dict[str, object]], payload["bars"]):
        cursor.execute(
            """
            INSERT INTO market_data_bars (
                manifest_sha256, generation, symbol, session_date, open_price,
                high_price, low_price, close_price, volume, currency,
                temporal_quality, source_receipt_sha256
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                accepted.manifest_sha256,
                generation,
                row["symbol"],
                row["sessionDate"],
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row["volume"],
                row["currency"],
                row["temporalQuality"],
                row["sourceReceiptSha256"],
            ),
        )
    for row in cast(list[dict[str, object]], payload["indices"]):
        cursor.execute(
            """
            INSERT INTO market_data_indices (
                manifest_sha256, generation, index_id, session_date, close_value,
                temporal_quality, source_receipt_sha256
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                accepted.manifest_sha256,
                generation,
                row["indexId"],
                row["sessionDate"],
                row["close"],
                row["temporalQuality"],
                row["sourceReceiptSha256"],
            ),
        )
    for row in cast(list[dict[str, object]], payload["macro"]):
        cursor.execute(
            """
            INSERT INTO market_data_macro (
                manifest_sha256, generation, series_id, observation_date, available_at,
                value_text, temporal_quality, source_receipt_sha256, entitlement_expires_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL)
            """,
            (
                accepted.manifest_sha256,
                generation,
                row["seriesId"],
                row["observationDate"],
                row["availableAt"],
                str(row["value"]),
                row["temporalQuality"],
                row["sourceReceiptSha256"],
            ),
        )
    for universe_row in accepted.universe_rows:
        cursor.execute(
            """
            INSERT INTO market_data_universes (
                manifest_sha256, generation, membership_month, selection_session,
                effective_from_session, instrument_id, symbol, market, rank,
                is_fixed_member, temporal_quality, source_receipt_sha256
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                accepted.manifest_sha256,
                generation,
                universe_row["membershipMonth"],
                universe_row["selectionSession"],
                universe_row["effectiveFromSession"],
                universe_row["instrumentId"],
                universe_row["symbol"],
                universe_row["market"],
                universe_row["rank"],
                universe_row["isFixedMember"],
                universe_row["temporalQuality"],
                universe_row["sourceReceiptSha256"],
            ),
        )
    return {
        "bars": len(cast(list[object], payload["bars"])),
        "indices": len(cast(list[object], payload["indices"])),
        "macro": len(cast(list[object], payload["macro"])),
        "universes": len(accepted.universe_rows),
    }


def _copy_artifacts(*, cursor: CursorLike, archive: MarketDataArchive) -> dict[str, int]:
    counts: dict[str, int] = {}
    for kind, statement, mapper in (
        (
            "BARS",
            """COPY market_data_bars (
                manifest_sha256, generation, symbol, session_date, open_price,
                high_price, low_price, close_price, volume, currency,
                temporal_quality, source_receipt_sha256
            ) FROM STDIN""",
            _bar_row,
        ),
        (
            "INDICES",
            """COPY market_data_indices (
                manifest_sha256, generation, index_id, session_date, close_value,
                temporal_quality, source_receipt_sha256
            ) FROM STDIN""",
            _index_row,
        ),
        (
            "MACRO",
            """COPY market_data_macro (
                manifest_sha256, generation, series_id, observation_date, available_at,
                value_text, temporal_quality, source_receipt_sha256, entitlement_expires_at
            ) FROM STDIN""",
            _macro_row,
        ),
        (
            "UNIVERSES",
            """COPY market_data_universes (
                manifest_sha256, generation, membership_month, selection_session,
                effective_from_session, instrument_id, symbol, market, rank,
                is_fixed_member, temporal_quality, source_receipt_sha256
            ) FROM STDIN""",
            _universe_row,
        ),
    ):
        artifact = archive.artifact(kind)
        table = read_artifact_table(archive, kind)
        rows = table.to_pylist()
        if len(rows) != artifact.row_count:
            raise MarketDataRepositoryError("verified artifact row count changed before DB copy")
        with cursor.copy(statement) as copy:
            for row in rows:
                copy.write_row(mapper(archive.manifest_sha256, row))
        counts[kind] = len(rows)
    return counts


def _bar_row(manifest_sha256: str, row: dict[str, object]) -> tuple[object, ...]:
    return (
        manifest_sha256,
        1,
        row["symbol"],
        row["sessionDate"],
        row["open"],
        row["high"],
        row["low"],
        row["close"],
        row["volume"],
        row["currency"],
        row["temporalQuality"],
        row["sourceReceiptSha256"],
    )


def _index_row(manifest_sha256: str, row: dict[str, object]) -> tuple[object, ...]:
    return (
        manifest_sha256,
        1,
        row["indexId"],
        row["sessionDate"],
        row["close"],
        row["temporalQuality"],
        row["sourceReceiptSha256"],
    )


def _macro_row(manifest_sha256: str, row: dict[str, object]) -> tuple[object, ...]:
    return (
        manifest_sha256,
        1,
        row["seriesId"],
        row["observationDate"],
        row["availableAt"],
        row["value"],
        row["temporalQuality"],
        row["sourceReceiptSha256"],
        None,
    )


def _universe_row(manifest_sha256: str, row: dict[str, object]) -> tuple[object, ...]:
    return (
        manifest_sha256,
        1,
        row["membershipMonth"],
        row["selectionSession"],
        row["effectiveFromSession"],
        row["instrumentId"],
        row["symbol"],
        row["market"],
        row["rank"],
        row["isFixedMember"],
        row["temporalQuality"],
        row["sourceReceiptSha256"],
    )
