"""Automation V3 read-only market-history bootstrap and bounded runtime reader.

Planning and fixture collection are provider neutral.  The only live adapter in
this module wraps the existing read-only KIS market client and is gated by an
explicit environment opt-in; account and order surfaces are absent.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from datetime import UTC, date, datetime
from io import BytesIO
from importlib.metadata import version
from pathlib import Path
from typing import Any, Protocol, cast

import pandas as pd
import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from app.data._shared.canonical_json import canonical_json_bytes, canonical_json_sha256
from app.data.calendar.xkrx_policy import corrected_calendar
from app.data.kis.http_client import KISHttpClient
from app.data.kis.market_client import KISMarketClient
from app.data.kis.parsers import DailyBar
from app.data.kis.settings import KISSettings
from app.data.kis.universe import UniverseManifest
from app.rag.safe_io import RagSafeIoError, read_approved_regular_file, write_approved_new_file

CONTRACT_ID = "p1-automation-market-bootstrap.v1"
MANIFEST_FILENAME = "manifest.json"
BAR_PATH = "bars/automation-bars-v1.parquet"
SESSION_COUNT = 1_260
WINDOW_SIZE = 100
UNIVERSE_SIZE = 31
KIS_DAILY_PHYSICAL_MAX = 403
KIS_TOKEN_PHYSICAL_MAX = 1
KRX_MEMBERSHIP_PHYSICAL_MAX = 5
_MAX_MANIFEST_BYTES = 256 * 1024
_MAX_BARS_BYTES = 256 * 1024 * 1024
_CALENDAR_REVISION = "XKRX-4.13.2+KIS_CTCA0903R"
_CALENDAR_SHA256 = hashlib.sha256(_CALENDAR_REVISION.encode("ascii")).hexdigest()
_BAR_COLUMNS = (
    "symbol",
    "market",
    "sessionDate",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "currency",
    "temporalQuality",
    "sourceReceiptSha256",
)


class AutomationBootstrapError(RuntimeError):
    """The bootstrap plan, normalized archive, role, or row coverage is unsafe."""


@dataclass(frozen=True, slots=True)
class BootstrapMember:
    symbol: str
    market: str
    rank: int
    is_fixed_member: bool


@dataclass(frozen=True, slots=True)
class BootstrapWindow:
    symbol: str
    market: str
    ordinal: int
    sessions: tuple[date, ...]

    @property
    def start_session(self) -> date:
        return self.sessions[0]

    @property
    def end_session(self) -> date:
        return self.sessions[-1]


@dataclass(frozen=True, slots=True)
class AutomationBootstrapPlan:
    membership_month: str
    selection_session: date
    source_sha256: str
    members: tuple[BootstrapMember, ...]
    sessions: tuple[date, ...]
    windows: tuple[BootstrapWindow, ...]
    plan_sha256: str
    provider_caps: dict[str, int]


class BootstrapBarSource(Protocol):
    physical_calls: int

    def fetch(self, window: BootstrapWindow) -> tuple[DailyBar, ...]: ...


@dataclass(frozen=True, slots=True)
class AutomationBootstrapArchive:
    root: Path
    manifest_sha256: str
    bars_sha256: str
    row_count: int
    manifest: dict[str, Any]
    bars: pa.Table


@dataclass(frozen=True, slots=True)
class AutomationBootstrapStageResult:
    manifest_sha256: str
    outcome: str
    bars: int
    universes: int
    provider_calls: int = 0


@dataclass(frozen=True, slots=True)
class AutomationMarketInventory:
    manifest_count: int
    bar_count: int
    current_universe_count: int
    latest_session: date | None
    status: str


@dataclass(frozen=True, slots=True)
class AutomationMarketBar:
    symbol: str
    session_date: date
    open_price: int
    high_price: int
    low_price: int
    close_price: int
    volume: int
    temporal_quality: str
    source_receipt_sha256: str


def build_bootstrap_plan(
    universe: UniverseManifest,
    *,
    end_session: date,
    session_count: int = SESSION_COUNT,
) -> AutomationBootstrapPlan:
    """Build exact-31 and up-to-1,260 fixed-100-session request windows with no I/O."""

    if version("exchange-calendars") != "4.13.2":
        raise AutomationBootstrapError("automation bootstrap XKRX version drifted")
    if universe.limit != 30 or len(universe.symbols) != 30:
        raise AutomationBootstrapError("automation bootstrap requires exact 30 ranked KRX members")
    if isinstance(session_count, bool) or not 23 <= session_count <= SESSION_COUNT:
        raise AutomationBootstrapError("automation bootstrap session count is invalid")
    ranks = [item.rank for item in universe.symbols]
    symbols = [item.symbol for item in universe.symbols]
    if ranks != list(range(1, 31)) or len(set(symbols)) != 30:
        raise AutomationBootstrapError("automation bootstrap ranking must be exact and unique")
    if any(
        len(item.symbol) != 6 or not item.symbol.isdigit() or item.market not in {"KOSPI", "KOSDAQ"}
        for item in universe.symbols
    ):
        raise AutomationBootstrapError("automation bootstrap member is invalid")
    if "132030" in symbols:
        raise AutomationBootstrapError("fixed gold ETF must not duplicate the ranked top 30")
    if len(universe.source_sha256) != 64:
        raise AutomationBootstrapError("automation bootstrap universe receipt is invalid")

    calendar = corrected_calendar()
    try:
        end = calendar.date_to_session(pd.Timestamp(end_session), direction="none")
        raw_sessions = calendar.sessions_window(end, -session_count)
    except Exception as error:
        raise AutomationBootstrapError(
            "automation bootstrap session window is unavailable"
        ) from error
    sessions = tuple(item.date() for item in raw_sessions)
    if len(sessions) != session_count or sessions[-1] != end_session:
        raise AutomationBootstrapError("automation bootstrap session window drifted")

    members = tuple(
        [BootstrapMember(item.symbol, item.market, item.rank, False) for item in universe.symbols]
        + [BootstrapMember("132030", "KOSPI", 31, True)]
    )
    windows = tuple(
        BootstrapWindow(
            member.symbol,
            member.market,
            ordinal,
            sessions[offset : offset + WINDOW_SIZE],
        )
        for member in members
        for ordinal, offset in enumerate(range(0, session_count, WINDOW_SIZE), start=1)
    )
    expected_kis_calls = UNIVERSE_SIZE * ((session_count + WINDOW_SIZE - 1) // WINDOW_SIZE)
    if len(windows) != expected_kis_calls or expected_kis_calls > KIS_DAILY_PHYSICAL_MAX:
        raise AutomationBootstrapError("automation bootstrap KIS window cap drifted")
    provider_caps = {
        "kisDaily": expected_kis_calls,
        "kisToken": KIS_TOKEN_PHYSICAL_MAX,
        "krxMembership": KRX_MEMBERSHIP_PHYSICAL_MAX,
        "retry": 0,
    }
    plan_value = {
        "contractId": "p1-automation-market-bootstrap-plan.v1",
        "endSession": end_session.isoformat(),
        "membership": [member.symbol for member in members],
        "membershipMonth": end_session.strftime("%Y-%m"),
        "providerCaps": provider_caps,
        "requestedSessionCount": session_count,
        "sourceSha256": universe.source_sha256,
        "windows": [
            {
                "endSession": window.end_session.isoformat(),
                "ordinal": window.ordinal,
                "startSession": window.start_session.isoformat(),
                "symbol": window.symbol,
            }
            for window in windows
        ],
    }
    return AutomationBootstrapPlan(
        membership_month=end_session.strftime("%Y-%m"),
        selection_session=end_session,
        source_sha256=universe.source_sha256,
        members=members,
        sessions=sessions,
        windows=windows,
        plan_sha256=canonical_json_sha256(plan_value),
        provider_caps=provider_caps,
    )


def collect_automation_bootstrap(
    *,
    plan: AutomationBootstrapPlan,
    source: BootstrapBarSource,
    output_root: Path,
    created_at: datetime,
    token_physical_calls: int,
    krx_membership_physical_calls: int,
) -> AutomationBootstrapArchive:
    """Collect bounded adjusted rows, validate complete tails, and publish manifest last."""

    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise AutomationBootstrapError("automation bootstrap createdAt must be timezone aware")
    if token_physical_calls not in {0, 1}:
        raise AutomationBootstrapError("automation bootstrap token call count is invalid")
    if not 0 <= krx_membership_physical_calls <= KRX_MEMBERSHIP_PHYSICAL_MAX:
        raise AutomationBootstrapError("automation bootstrap KRX call count is invalid")
    rows_by_identity: dict[tuple[str, date], tuple[DailyBar, str]] = {}
    for window in plan.windows:
        fetched = source.fetch(window)
        receipt = _window_receipt(window, fetched)
        for row in fetched:
            _validate_bar(row, window)
            identity = (row.symbol, row.date)
            existing = rows_by_identity.get(identity)
            candidate = (row, receipt)
            if existing is not None and existing != candidate:
                raise AutomationBootstrapError("automation bootstrap conflicting duplicate row")
            rows_by_identity[identity] = candidate
    if source.physical_calls > plan.provider_caps["kisDaily"]:
        raise AutomationBootstrapError("automation bootstrap KIS calls exceeded the plan")

    expected_set = set(plan.sessions)
    normalized: list[dict[str, object]] = []
    for member in plan.members:
        observed = sorted(day for symbol, day in rows_by_identity if symbol == member.symbol)
        if not observed:
            raise AutomationBootstrapError("automation bootstrap symbol history is empty")
        if not set(observed).issubset(expected_set):
            raise AutomationBootstrapError("automation bootstrap returned an out-of-window session")
        first_index = plan.sessions.index(observed[0])
        expected_tail = plan.sessions[first_index:]
        if tuple(observed) != expected_tail:
            raise AutomationBootstrapError("automation bootstrap middle session gap detected")
        for session in observed:
            row, receipt = rows_by_identity[(member.symbol, session)]
            normalized.append(
                {
                    "symbol": row.symbol,
                    "market": member.market,
                    "sessionDate": row.date,
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": row.volume,
                    "currency": "KRW",
                    "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
                    "sourceReceiptSha256": receipt,
                }
            )
    normalized.sort(key=lambda item: (cast(str, item["symbol"]), cast(date, item["sessionDate"])))
    table = _bar_table(normalized)
    payload = _parquet_bytes(table)
    bars_sha = hashlib.sha256(payload).hexdigest()
    manifest_without_sha: dict[str, object] = {
        "accountCalls": 0,
        "adjustmentMode": "ADJUSTED",
        "bars": {
            "relativePath": BAR_PATH,
            "rowCount": table.num_rows,
            "sha256": bars_sha,
        },
        "complete": True,
        "contractId": CONTRACT_ID,
        "createdAt": created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "firstSessionDate": min(cast(list[date], table["sessionDate"].to_pylist())).isoformat(),
        "lastSessionDate": max(cast(list[date], table["sessionDate"].to_pylist())).isoformat(),
        "membership": [member.symbol for member in plan.members],
        "membershipMonth": plan.membership_month,
        "orderCalls": 0,
        "performanceClaimAllowed": False,
        "providerCaps": plan.provider_caps,
        "providerPhysicalCalls": {
            "kisDaily": source.physical_calls,
            "kisToken": token_physical_calls,
            "krxMembership": krx_membership_physical_calls,
        },
        "rawProviderResponseStored": False,
        "requestedSessionCount": len(plan.sessions),
        "sourcePathPersisted": False,
    }
    manifest_sha = canonical_json_sha256(manifest_without_sha)
    manifest = {**manifest_without_sha, "manifestSha256": manifest_sha}
    _publish_archive(output_root, payload, canonical_json_bytes(manifest))
    return read_automation_bootstrap_archive(output_root)


def read_automation_bootstrap_archive(root: Path) -> AutomationBootstrapArchive:
    try:
        manifest_file = read_approved_regular_file(
            approved_root=root,
            relative_path=MANIFEST_FILENAME,
            max_bytes=_MAX_MANIFEST_BYTES,
        )
        bars_file = read_approved_regular_file(
            approved_root=root,
            relative_path=BAR_PATH,
            max_bytes=_MAX_BARS_BYTES,
        )
    except RagSafeIoError as error:
        raise AutomationBootstrapError(
            "automation bootstrap archive boundary is invalid"
        ) from error
    try:
        manifest = json.loads(manifest_file.content)
    except json.JSONDecodeError as error:
        raise AutomationBootstrapError("automation bootstrap manifest is invalid JSON") from error
    if (
        not isinstance(manifest, dict)
        or canonical_json_bytes(manifest) != manifest_file.content
        or manifest.get("contractId") != CONTRACT_ID
        or manifest.get("complete") is not True
    ):
        raise AutomationBootstrapError("automation bootstrap manifest is not closed canonical JSON")
    declared_sha = manifest.get("manifestSha256")
    without_sha = dict(manifest)
    without_sha.pop("manifestSha256", None)
    if (
        declared_sha != canonical_json_sha256(without_sha)
        or declared_sha != manifest_file.content_sha256
    ):
        # The external identity is the full manifest bytes; the embedded identity binds the closed body.
        # Keep both identities separate instead of making the document self-referential.
        if declared_sha != canonical_json_sha256(without_sha):
            raise AutomationBootstrapError("automation bootstrap manifest identity drifted")
    bars_receipt = manifest.get("bars")
    if not isinstance(bars_receipt, dict):
        raise AutomationBootstrapError("automation bootstrap bars receipt is invalid")
    if bars_receipt.get("sha256") != bars_file.content_sha256:
        raise AutomationBootstrapError("automation bootstrap bars digest mismatch")
    try:
        table = pq.read_table(pa.BufferReader(bars_file.content))  # type: ignore[no-untyped-call]
    except (pa.ArrowException, OSError) as error:
        raise AutomationBootstrapError("automation bootstrap bars are not valid Parquet") from error
    if tuple(table.column_names) != _BAR_COLUMNS or table.num_rows != bars_receipt.get("rowCount"):
        raise AutomationBootstrapError("automation bootstrap bars shape drifted")
    membership = manifest.get("membership")
    if (
        not isinstance(membership, list)
        or len(membership) != UNIVERSE_SIZE
        or len(set(membership)) != UNIVERSE_SIZE
        or membership[-1] != "132030"
    ):
        raise AutomationBootstrapError("automation bootstrap membership drifted")
    return AutomationBootstrapArchive(
        root=root,
        manifest_sha256=manifest_file.content_sha256,
        bars_sha256=bars_file.content_sha256,
        row_count=table.num_rows,
        manifest=cast(dict[str, Any], manifest),
        bars=table,
    )


def stage_automation_bootstrap(
    *,
    database_dsn: str,
    archive_root: Path,
    expected_manifest_sha256: str,
) -> AutomationBootstrapStageResult:
    """Verify archive identity before opening the least-privilege writer connection."""

    archive = read_automation_bootstrap_archive(archive_root)
    if archive.manifest_sha256 != expected_manifest_sha256:
        raise AutomationBootstrapError(
            "automation bootstrap manifest does not match operator binding"
        )
    with psycopg.connect(database_dsn, autocommit=False, connect_timeout=2) as connection:
        cursor = connection.cursor()
        try:
            cursor.execute("select session_user,current_user")
            if cursor.fetchone() != ("decision_market_writer", "decision_market_writer"):
                raise AutomationBootstrapError("automation bootstrap DB role must be market writer")
            manifest = archive.manifest
            membership_sha = canonical_json_sha256(manifest["membership"])
            created_at = datetime.fromisoformat(str(manifest["createdAt"]).replace("Z", "+00:00"))
            last_session = date.fromisoformat(str(manifest["lastSessionDate"]))
            cursor.execute(
                """
                insert into market_data_manifests (
                  manifest_sha256,manifest_kind,contract_id,session_date,as_of,generation,
                  source_manifest_sha256,previous_manifest_sha256,supersedes_sha256,
                  archive_sha256,receipt_set_sha256,calendar_revision,calendar_sha256,
                  temporal_quality,entitlement_expires_at,status
                ) values (%s,'AUTOMATION_BOOTSTRAP',%s,%s,%s,1,%s,null,null,%s,null,%s,%s,
                          'RECONSTRUCTED_FIXED_LAG',null,'ACCEPTED')
                """,
                (
                    archive.manifest_sha256,
                    CONTRACT_ID,
                    last_session,
                    created_at,
                    membership_sha,
                    archive.bars_sha256,
                    _CALENDAR_REVISION,
                    _CALENDAR_SHA256,
                ),
            )
            if cursor.rowcount == 0:
                connection.commit()
                return AutomationBootstrapStageResult(archive.manifest_sha256, "NO_OP", 0, 0)
            with cursor.copy(
                """copy market_data_bars (
                  manifest_sha256,generation,symbol,session_date,open_price,high_price,
                  low_price,close_price,volume,currency,temporal_quality,source_receipt_sha256
                ) from stdin"""
            ) as copy:
                for row in archive.bars.to_pylist():
                    copy.write_row(
                        (
                            archive.manifest_sha256,
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
                    )
            market_by_symbol = {
                str(row["symbol"]): str(row["market"]) for row in archive.bars.to_pylist()
            }
            membership = cast(list[str], manifest["membership"])
            membership_month = str(manifest["membershipMonth"])
            for rank, symbol in enumerate(membership, start=1):
                cursor.execute(
                    """
                    insert into market_data_universes (
                      manifest_sha256,generation,membership_month,selection_session,
                      effective_from_session,instrument_id,symbol,market,rank,is_fixed_member,
                      temporal_quality,source_receipt_sha256
                    ) values (%s,1,%s,%s,%s,%s,%s,%s,%s,%s,'RECONSTRUCTED_FIXED_LAG',%s)
                    """,
                    (
                        archive.manifest_sha256,
                        membership_month,
                        last_session,
                        last_session,
                        "XKRX:ETF:132030" if symbol == "132030" else f"KRX{symbol}000",
                        symbol,
                        market_by_symbol[symbol],
                        rank,
                        symbol == "132030",
                        membership_sha,
                    ),
                )
            connection.commit()
            return AutomationBootstrapStageResult(
                archive.manifest_sha256,
                "INSERTED",
                archive.row_count,
                len(membership),
            )
        except Exception:
            connection.rollback()
            raise


class PostgresAutomationMarketReader:
    """Automation runtime can call two definer functions and no market-data table."""

    def __init__(self, database_dsn: str) -> None:
        parsed = conninfo_to_dict(database_dsn)
        if (
            parsed.get("user") != "decision_automation_runtime"
            or parsed.get("host") not in {"postgres", "127.0.0.1", "localhost"}
            or not parsed.get("dbname")
        ):
            raise AutomationBootstrapError("automation market reader DSN role is invalid")
        self._database_dsn = database_dsn

    def inventory(self) -> AutomationMarketInventory:
        with self._connect(row_factory=dict_row) as connection, connection.cursor() as cursor:
            cursor.execute("select * from p1_read_automation_market_history_status_v1()")
            row = cursor.fetchone()
        if row is None:
            raise AutomationBootstrapError("automation market inventory is unavailable")
        return AutomationMarketInventory(
            manifest_count=int(row["manifest_count"]),
            bar_count=int(row["bar_count"]),
            current_universe_count=int(row["current_universe_count"]),
            latest_session=cast(date | None, row["latest_session"]),
            status=str(row["history_status"]),
        )

    def read_atr_bars(
        self,
        symbol: str,
        *,
        as_of_session: date,
        limit: int,
    ) -> tuple[AutomationMarketBar, ...]:
        if (
            len(symbol) != 6
            or not symbol.isdigit()
            or isinstance(limit, bool)
            or not 1 <= limit <= 101
        ):
            raise AutomationBootstrapError("automation ATR read request is invalid")
        with self._connect(row_factory=dict_row) as connection, connection.cursor() as cursor:
            cursor.execute(
                "select * from p1_read_automation_atr_bars_v1(%s,%s,%s)",
                (symbol, as_of_session, limit),
            )
            rows = cursor.fetchall()
        result = tuple(
            AutomationMarketBar(
                symbol=str(row["symbol"]),
                session_date=cast(date, row["session_date"]),
                open_price=int(row["open_price"]),
                high_price=int(row["high_price"]),
                low_price=int(row["low_price"]),
                close_price=int(row["close_price"]),
                volume=int(row["volume"]),
                temporal_quality=str(row["temporal_quality"]),
                source_receipt_sha256=str(row["source_receipt_sha256"]),
            )
            for row in rows
        )
        if tuple(item.session_date for item in result) != tuple(
            sorted(item.session_date for item in result)
        ):
            raise AutomationBootstrapError("automation ATR history order drifted")
        return result

    def close(self) -> None:
        """Connections are per-call context managers; the port remains explicitly closeable."""

    def _connect(self, *, row_factory: Any | None = None) -> psycopg.Connection[Any]:
        options: dict[str, Any] = {"autocommit": False, "connect_timeout": 2}
        if row_factory is not None:
            options["row_factory"] = row_factory
        return psycopg.connect(self._database_dsn, **options)


class KisAutomationBootstrapSource:
    """Read-only live KIS adapter; construction requires an exact explicit opt-in."""

    def __init__(self, client: KISMarketClient) -> None:
        self._client = client
        self.physical_calls = 0

    @classmethod
    def from_environment(cls) -> KisAutomationBootstrapSource:
        if os.environ.get("P1_AUTOMATION_MARKET_BOOTSTRAP_ENABLED", "false").lower() != "true":
            raise AutomationBootstrapError("automation live market bootstrap is disabled")
        mode = os.environ.get("P1_AUTOMATION_MARKET_BOOTSTRAP_KIS_MODE", "live").lower()
        if mode not in {"mock", "live"}:
            raise AutomationBootstrapError("automation market bootstrap KIS mode is invalid")
        if mode == "mock" and os.environ.get("KIS_MOCK_CONFIGURED", "false").lower() != "true":
            raise AutomationBootstrapError("automation mock market bootstrap is not configured")
        settings = KISSettings(kis_mode=cast(Any, mode), kis_offline=False, kis_retry_attempts=1)
        http = KISHttpClient(settings)
        return cls(KISMarketClient(settings, http, page_size=100))

    def fetch(self, window: BootstrapWindow) -> tuple[DailyBar, ...]:
        if self.physical_calls >= KIS_DAILY_PHYSICAL_MAX:
            raise AutomationBootstrapError("automation bootstrap KIS call cap exhausted")
        self.physical_calls += 1
        return tuple(
            self._client.daily_bars(
                window.symbol,
                window.start_session,
                window.end_session,
            )
        )

    def close(self) -> None:
        self._client.close()


def _validate_bar(row: DailyBar, window: BootstrapWindow) -> None:
    if (
        row.symbol != window.symbol
        or row.date not in window.sessions
        or row.open <= 0
        or row.high < max(row.open, row.close)
        or row.low <= 0
        or row.low > min(row.open, row.close)
        or row.close <= 0
        or row.volume < 0
    ):
        raise AutomationBootstrapError("automation bootstrap normalized bar is invalid")


def _window_receipt(window: BootstrapWindow, rows: tuple[DailyBar, ...]) -> str:
    return canonical_json_sha256(
        {
            "operation": "FHKST03010100",
            "ordinal": window.ordinal,
            "rows": [
                {
                    "close": row.close,
                    "date": row.date.isoformat(),
                    "high": row.high,
                    "low": row.low,
                    "open": row.open,
                    "symbol": row.symbol,
                    "volume": row.volume,
                }
                for row in rows
            ],
            "start": window.start_session.isoformat(),
            "end": window.end_session.isoformat(),
            "symbol": window.symbol,
        }
    )


def _bar_table(rows: list[dict[str, object]]) -> pa.Table:
    schema = pa.schema(
        [
            pa.field("symbol", pa.string(), nullable=False),
            pa.field("market", pa.string(), nullable=False),
            pa.field("sessionDate", pa.date32(), nullable=False),
            pa.field("open", pa.int64(), nullable=False),
            pa.field("high", pa.int64(), nullable=False),
            pa.field("low", pa.int64(), nullable=False),
            pa.field("close", pa.int64(), nullable=False),
            pa.field("volume", pa.int64(), nullable=False),
            pa.field("currency", pa.string(), nullable=False),
            pa.field("temporalQuality", pa.string(), nullable=False),
            pa.field("sourceReceiptSha256", pa.string(), nullable=False),
        ]
    )
    return pa.Table.from_pylist(rows, schema=schema)


def _parquet_bytes(table: pa.Table) -> bytes:
    sink = BytesIO()
    pq.write_table(  # type: ignore[no-untyped-call]
        table.replace_schema_metadata(None),
        sink,
        version="2.6",
        data_page_version="1.0",
        compression="zstd",
        compression_level=3,
        use_dictionary=False,
        row_group_size=8_192,
        write_statistics=True,
    )
    payload = sink.getvalue()
    if not payload or len(payload) > _MAX_BARS_BYTES:
        raise AutomationBootstrapError("automation bootstrap bars exceed the physical bound")
    return payload


def _publish_archive(root: Path, bars: bytes, manifest: bytes) -> None:
    if not root.is_absolute() or ".." in root.parts or root.anchor != "/" or root.exists():
        raise AutomationBootstrapError(
            "automation bootstrap output root must be a new absolute path"
        )
    parent = root.parent
    current = Path("/")
    for component in parent.parts[1:]:
        current /= component
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise AutomationBootstrapError("automation bootstrap output parent contains a symlink")
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    (root / "bars").mkdir(mode=0o700)
    try:
        write_approved_new_file(
            approved_root=root,
            relative_path=BAR_PATH,
            content=bars,
            max_bytes=_MAX_BARS_BYTES,
        )
        write_approved_new_file(
            approved_root=root,
            relative_path=MANIFEST_FILENAME,
            content=manifest,
            max_bytes=_MAX_MANIFEST_BYTES,
        )
    except RagSafeIoError as error:
        raise AutomationBootstrapError("automation bootstrap publication failed") from error
