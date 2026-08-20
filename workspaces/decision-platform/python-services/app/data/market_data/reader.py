"""Bounded internal readers over verified stored market-data archives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, cast

from app.data.market_data.archive import (
    MarketDataArchive,
    MarketDataArchiveError,
    OPERATIONAL_HISTORY_MAX,
    RESEARCH_HISTORY_MAX,
    read_artifact_table,
    read_market_data_archive,
)


@dataclass(frozen=True, slots=True)
class CloseObservation:
    identity: str
    session_date: date
    close: Decimal
    temporal_quality: str
    source_receipt_sha256: str


class MarketDataOperationalReader(Protocol):
    """Current monthly exact-31 and at most 253 stored closes; provider calls are impossible."""

    def current_symbols(self) -> tuple[str, ...]: ...

    def read_closes(
        self, symbol: str, *, limit: int = OPERATIONAL_HISTORY_MAX
    ) -> tuple[CloseObservation, ...]: ...


class ResearchMarketHistoryReader(Protocol):
    """Offline research history bounded to 1,260 XKRX sessions."""

    def read_symbol_closes(
        self, symbol: str, *, limit: int = RESEARCH_HISTORY_MAX
    ) -> tuple[CloseObservation, ...]: ...


class QueryCursorLike(Protocol):
    def fetchone(self) -> tuple[object, ...] | None: ...

    def fetchall(self) -> list[tuple[object, ...]]: ...


class QueryConnectionLike(Protocol):
    def execute(
        self, query: str, params: tuple[object, ...] = ()
    ) -> QueryCursorLike: ...

    def read_index_closes(
        self, index_id: str, *, limit: int = RESEARCH_HISTORY_MAX
    ) -> tuple[CloseObservation, ...]: ...


class ParquetMarketDataOperationalReader:
    """Archive-backed operational port with no network/provider dependency."""

    def __init__(self, root: Path) -> None:
        self._archive = read_market_data_archive(root)
        self._symbols = _latest_month_symbols(self._archive)
        if len(self._symbols) != 31:
            raise MarketDataArchiveError("operational universe must contain exact 31 symbols")
        self._bars = _group_rows(
            read_artifact_table(self._archive, "BARS").to_pylist(),
            identity_field="symbol",
        )

    def current_symbols(self) -> tuple[str, ...]:
        return self._symbols

    def read_closes(
        self, symbol: str, *, limit: int = OPERATIONAL_HISTORY_MAX
    ) -> tuple[CloseObservation, ...]:
        if symbol not in self._symbols:
            raise MarketDataArchiveError("symbol is outside the current exact-31 universe")
        return _observations(
            self._bars,
            identity=symbol,
            limit=_bounded(limit, OPERATIONAL_HISTORY_MAX),
        )


class ParquetResearchMarketHistoryReader:
    """Offline-only research port; application/Spring credentials never use this class."""

    def __init__(self, root: Path) -> None:
        self._archive = read_market_data_archive(root)
        self._bars = _group_rows(
            read_artifact_table(self._archive, "BARS").to_pylist(),
            identity_field="symbol",
        )
        self._indices = _group_rows(
            read_artifact_table(self._archive, "INDICES").to_pylist(),
            identity_field="indexId",
        )

    def read_symbol_closes(
        self, symbol: str, *, limit: int = RESEARCH_HISTORY_MAX
    ) -> tuple[CloseObservation, ...]:
        return _observations(
            self._bars,
            identity=symbol,
            limit=_bounded(limit, RESEARCH_HISTORY_MAX),
        )

    def read_index_closes(
        self, index_id: str, *, limit: int = RESEARCH_HISTORY_MAX
    ) -> tuple[CloseObservation, ...]:
        if index_id not in {"KOSPI", "KOSDAQ"}:
            raise MarketDataArchiveError("research index must be KOSPI or KOSDAQ")
        return _observations(
            self._indices,
            identity=index_id,
            limit=_bounded(limit, RESEARCH_HISTORY_MAX),
        )


class PostgresMarketDataOperationalReader:
    """Read only the two bounded security-barrier views under the operational role."""

    def __init__(self, connection: QueryConnectionLike) -> None:
        _require_current_role(connection, "decision_market_operational_reader")
        self._connection = connection
        self._symbols = tuple(
            str(row[0])
            for row in connection.execute(
                "select symbol from market_data_operational_universe order by rank"
            ).fetchall()
        )
        if len(self._symbols) != 31 or len(set(self._symbols)) != 31:
            raise MarketDataArchiveError("stored operational universe must be exact 31")

    def current_symbols(self) -> tuple[str, ...]:
        return self._symbols

    def read_closes(
        self, symbol: str, *, limit: int = OPERATIONAL_HISTORY_MAX
    ) -> tuple[CloseObservation, ...]:
        if symbol not in self._symbols:
            raise MarketDataArchiveError("symbol is outside the current exact-31 universe")
        rows = self._connection.execute(
            """
            select symbol, session_date, close_price, temporal_quality, source_receipt_sha256
            from market_data_operational_bars
            where symbol = %s
            order by session_date desc
            limit %s
            """,
            (symbol, _bounded(limit, OPERATIONAL_HISTORY_MAX)),
        ).fetchall()
        return _db_observations(reversed(rows))


class PostgresResearchMarketHistoryReader:
    """Read the bounded research views only; Spring application credentials are rejected."""

    def __init__(self, connection: QueryConnectionLike) -> None:
        _require_current_role(connection, "decision_market_research_reader")
        self._connection = connection

    def read_symbol_closes(
        self, symbol: str, *, limit: int = RESEARCH_HISTORY_MAX
    ) -> tuple[CloseObservation, ...]:
        rows = self._connection.execute(
            """
            select symbol, session_date, close_price, temporal_quality, source_receipt_sha256
            from market_data_research_bars
            where symbol = %s
            order by session_date desc
            limit %s
            """,
            (symbol, _bounded(limit, RESEARCH_HISTORY_MAX)),
        ).fetchall()
        return _db_observations(reversed(rows))

    def read_index_closes(
        self, index_id: str, *, limit: int = RESEARCH_HISTORY_MAX
    ) -> tuple[CloseObservation, ...]:
        if index_id not in {"KOSPI", "KOSDAQ"}:
            raise MarketDataArchiveError("research index must be KOSPI or KOSDAQ")
        rows = self._connection.execute(
            """
            select index_id, session_date, close_value, temporal_quality,
                   source_receipt_sha256
            from market_data_research_indices
            where index_id = %s
            order by session_date desc
            limit %s
            """,
            (index_id, _bounded(limit, RESEARCH_HISTORY_MAX)),
        ).fetchall()
        return _db_observations(reversed(rows))


def _latest_month_symbols(archive: MarketDataArchive) -> tuple[str, ...]:
    table = read_artifact_table(archive, "UNIVERSES")
    rows = table.to_pylist()
    if not rows:
        raise MarketDataArchiveError("universe archive is empty")
    latest = max(str(row["membershipMonth"]) for row in rows)
    selected = sorted(
        (row for row in rows if str(row["membershipMonth"]) == latest),
        key=lambda row: int(row["rank"]),
    )
    symbols = tuple(str(row["symbol"]) for row in selected)
    if len(symbols) != len(set(symbols)):
        raise MarketDataArchiveError("latest universe contains duplicate symbols")
    return symbols


def _group_rows(
    rows: list[dict[str, object]], *, identity_field: str
) -> dict[str, tuple[dict[str, object], ...]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row[identity_field]), []).append(row)
    return {
        identity: tuple(sorted(values, key=lambda row: cast(date, row["sessionDate"])))
        for identity, values in grouped.items()
    }


def _observations(
    grouped: dict[str, tuple[dict[str, object], ...]],
    *,
    identity: str,
    limit: int,
) -> tuple[CloseObservation, ...]:
    selected = grouped.get(identity, ())[-limit:]
    return tuple(
        CloseObservation(
            identity=identity,
            session_date=cast(date, row["sessionDate"]),
            close=Decimal(str(row["close"])),
            temporal_quality=str(row["temporalQuality"]),
            source_receipt_sha256=str(row["sourceReceiptSha256"]),
        )
        for row in selected
    )


def _bounded(value: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise MarketDataArchiveError(f"history limit must be within 1..{maximum}")
    return value


def _require_current_role(connection: QueryConnectionLike, expected: str) -> None:
    row = connection.execute("select current_user").fetchone()
    if row != (expected,):
        raise MarketDataArchiveError(f"stored reader requires current role {expected}")


def _db_observations(rows: Any) -> tuple[CloseObservation, ...]:
    return tuple(
        CloseObservation(
            identity=str(row[0]),
            session_date=cast(date, row[1]),
            close=Decimal(str(row[2])),
            temporal_quality=str(row[3]),
            source_receipt_sha256=str(row[4]),
        )
        for row in rows
    )
