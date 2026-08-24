from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import psycopg
import pytest
from jsonschema import Draft202012Validator

from app.data._shared.canonical_json import canonical_json_sha256
from app.data.market_data.daily_runtime import (
    AcceptedDailyShard,
    DailyMarketDataError,
    DailyReplayPacket,
    ReplayRecord,
    SealedDirectoryReplay,
    evidence_clock_for_session,
    operation_ids,
    run_offline_daily,
    write_replay_record,
)
from app.data.market_data.repository import stage_daily_shard
from app.verification.network_guard import deny_outbound_network

_ROOT = Path(__file__).resolve().parents[6]
_KST = ZoneInfo("Asia/Seoul")
_CALENDAR_REVISION = "XKRX-4.13.2+KIS_CTCA0903R"
_SHA = "a" * 64


class _Sink:
    def __init__(self, *, head_sha256: str = _SHA) -> None:
        self.preflights = 0
        self.adoptions: list[AcceptedDailyShard] = []
        self.head_sha256 = head_sha256

    def preflight(self, packet: DailyReplayPacket) -> None:
        self.preflights += 1
        if packet.previous_accepted_manifest_sha256 != self.head_sha256:
            raise DailyMarketDataError("previous accepted manifest is not the sink head")

    def adopt(self, accepted: AcceptedDailyShard) -> str:
        self.adoptions.append(accepted)
        return "INSERTED"


def test_normal_replay_publishes_exact_daily_contract_manifest_last(tmp_path: Path) -> None:
    packet, records = _packet_and_records()
    replay_root = _seal(tmp_path / "replay", records)
    sink = _Sink()

    with deny_outbound_network():
        result = run_offline_daily(
            packet=packet,
            run_root=tmp_path / "runs",
            replay_factory=lambda: SealedDirectoryReplay(replay_root),
            sink=sink,
        )

    assert result.status == "ACCEPTED"
    assert result.replay_reads == 38
    assert result.provider_physical_calls == 0
    assert sink.preflights == 1
    assert len(sink.adoptions) == 1
    accepted = cast(AcceptedDailyShard, result.accepted)
    assert len(cast(list[object], accepted.payload["bars"])) == 31
    assert len(cast(list[object], accepted.payload["sourceReceipts"])) == 38
    assert accepted.universe_rows == ()
    _validate_contract("market-data-daily-shard.v1.schema.json", accepted.payload)
    _validate_contract("market-data-health.v1.schema.json", result.health)
    run = tmp_path / "runs" / packet.packet_sha256
    assert (run / "daily-shard.json").is_file()
    assert len(list((run / "staging").glob("*.json"))) == 38


def test_month_boundary_derives_41_operations_and_exact_universe(tmp_path: Path) -> None:
    packet, records = _packet_and_records(month_boundary=True)
    assert len(operation_ids(packet)) == 41
    result = run_offline_daily(
        packet=packet,
        run_root=tmp_path / "runs",
        replay_factory=lambda: SealedDirectoryReplay(_seal(tmp_path / "replay", records)),
        sink=_Sink(),
    )

    accepted = cast(AcceptedDailyShard, result.accepted)
    assert result.status == "ACCEPTED"
    assert result.replay_reads == 41
    assert len(accepted.universe_rows) == 31
    assert accepted.universe_rows[-1]["symbol"] == "132030"
    assert accepted.universe_rows[-1]["isFixedMember"] is True


def test_holiday_and_pre_evidence_clock_never_construct_replay_port(tmp_path: Path) -> None:
    packet, _ = _packet_and_records()
    constructed = 0

    def factory() -> SealedDirectoryReplay:
        nonlocal constructed
        constructed += 1
        raise AssertionError("replay root must not be opened")

    holiday = replace(
        packet,
        session_date=date(2026, 8, 17),
        membership_month="2026-08",
        checked_at=datetime(2026, 8, 18, 12, tzinfo=_KST),
    )
    holiday_result = run_offline_daily(
        packet=holiday, run_root=tmp_path / "holiday", replay_factory=factory, sink=_Sink()
    )
    waiting = replace(packet, checked_at=datetime(2026, 8, 19, 8, 9, tzinfo=_KST))
    waiting_result = run_offline_daily(
        packet=waiting, run_root=tmp_path / "waiting", replay_factory=factory, sink=_Sink()
    )

    assert holiday_result.status == "NO_NEW_SESSION"
    assert waiting_result.status == "WAITING_FOR_EVIDENCE_CLOCK"
    assert holiday_result.provider_physical_calls == 0
    assert waiting_result.provider_physical_calls == 0
    assert constructed == 0
    assert evidence_clock_for_session(date(2026, 8, 14)) == datetime(
        2026, 8, 18, 8, 10, tzinfo=_KST
    )


def test_empty_krx_projection_stops_before_all_later_replay_operations(tmp_path: Path) -> None:
    packet, records = _packet_and_records()
    first = records[0]
    empty_payload = {**first.payload, "nonEmpty": False}
    records[0] = replace(
        first, payload=empty_payload, content_sha256=canonical_json_sha256(empty_payload)
    )
    packet = _bind_receipts(packet, records)
    replay_root = _seal(tmp_path / "replay", records)

    result = run_offline_daily(
        packet=packet,
        run_root=tmp_path / "runs",
        replay_factory=lambda: SealedDirectoryReplay(replay_root),
        sink=_Sink(),
    )

    assert result.status == "CALENDAR_DIVERGENCE_SUSPECTED"
    assert result.replay_reads == 1
    assert result.accepted is None
    assert not (tmp_path / "runs" / packet.packet_sha256 / "daily-shard.json").exists()


def test_partial_run_has_no_manifest_and_resume_reuses_successes(tmp_path: Path) -> None:
    packet, records = _packet_and_records()
    missing_id = "KIS_DAILY_000003"
    missing = next(record for record in records if record.operation_id == missing_id)
    replay_root = _seal(
        tmp_path / "replay", [record for record in records if record.operation_id != missing_id]
    )
    run_root = tmp_path / "runs"
    sink = _Sink()

    first = run_offline_daily(
        packet=packet,
        run_root=run_root,
        replay_factory=lambda: SealedDirectoryReplay(replay_root),
        sink=sink,
    )
    assert first.status == "EVIDENCE_GAP"
    assert first.replay_reads == 7
    assert sink.adoptions == []
    assert not (run_root / packet.packet_sha256 / "daily-shard.json").exists()

    write_replay_record(replay_root, missing)
    resumed = run_offline_daily(
        packet=packet,
        run_root=run_root,
        replay_factory=lambda: SealedDirectoryReplay(replay_root),
        sink=sink,
    )
    assert resumed.status == "ACCEPTED"
    assert resumed.replay_reads == 31
    assert len(sink.adoptions) == 1


def test_mid_month_membership_change_fails_before_replay_root(tmp_path: Path) -> None:
    packet, _ = _packet_and_records()
    changed = ("999999", *packet.membership[1:])
    invalid = replace(packet, membership=changed)
    constructed = False

    def factory() -> SealedDirectoryReplay:
        nonlocal constructed
        constructed = True
        raise AssertionError

    try:
        run_offline_daily(
            packet=invalid, run_root=tmp_path / "runs", replay_factory=factory, sink=_Sink()
        )
    except Exception as error:
        assert "mid-month membership" in str(error)
    else:
        raise AssertionError("changed mid-month membership must fail closed")
    assert constructed is False


def test_receipt_binding_mismatch_needs_human_without_manifest_or_db(tmp_path: Path) -> None:
    packet, records = _packet_and_records()
    packet = replace(packet, expected_receipt_set_sha256="f" * 64)
    sink = _Sink()
    result = run_offline_daily(
        packet=packet,
        run_root=tmp_path / "runs",
        replay_factory=lambda: SealedDirectoryReplay(_seal(tmp_path / "replay", records)),
        sink=sink,
    )

    assert result.status == "NEEDS_HUMAN"
    assert result.provider_physical_calls == 0
    assert sink.adoptions == []
    assert not (tmp_path / "runs" / packet.packet_sha256 / "daily-shard.json").exists()


def test_stale_previous_manifest_fails_before_replay_or_run_root(tmp_path: Path) -> None:
    packet, records = _packet_and_records()
    constructed = False

    def factory() -> SealedDirectoryReplay:
        nonlocal constructed
        constructed = True
        return SealedDirectoryReplay(_seal(tmp_path / "replay", records))

    with pytest.raises(DailyMarketDataError, match="sink head"):
        run_offline_daily(
            packet=packet,
            run_root=tmp_path / "runs",
            replay_factory=factory,
            sink=_Sink(head_sha256="f" * 64),
        )

    assert constructed is False
    assert not (tmp_path / "runs").exists()


def test_replay_root_symlink_is_rejected(tmp_path: Path) -> None:
    packet, records = _packet_and_records()
    real_root = _seal(tmp_path / "real-replay", records)
    replay_link = tmp_path / "replay-link"
    replay_link.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(DailyMarketDataError, match="symlink"):
        run_offline_daily(
            packet=packet,
            run_root=tmp_path / "runs",
            replay_factory=lambda: SealedDirectoryReplay(replay_link),
            sink=_Sink(),
        )


def test_daily_db_adoption_is_atomic_idempotent_and_conflict_closed(
    tmp_path: Path, isolated_postgres_cluster: dict[str, str]
) -> None:
    packet, records = _packet_and_records()
    result = run_offline_daily(
        packet=packet,
        run_root=tmp_path / "runs",
        replay_factory=lambda: SealedDirectoryReplay(_seal(tmp_path / "replay", records)),
        sink=_Sink(),
    )
    accepted = cast(AcceptedDailyShard, result.accepted)
    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        connection.execute(
            """
            INSERT INTO market_data_manifests (
                manifest_sha256, manifest_kind, contract_id, session_date, as_of,
                generation, source_manifest_sha256, archive_sha256,
                calendar_revision, calendar_sha256, temporal_quality
            ) VALUES (%s, 'SEED', 'market-data-seed.v1', %s,
                      timestamptz '2026-08-18 00:00:00+00', 1, %s, %s, %s, %s,
                      'RECONSTRUCTED_FIXED_LAG')
            """,
            (
                _SHA,
                packet.previous_session_date,
                "1" * 64,
                "2" * 64,
                _CALENDAR_REVISION,
                "3" * 64,
            ),
        )

    inserted = stage_daily_shard(
        database_dsn=isolated_postgres_cluster["market_writer_dsn"],
        accepted=accepted,
        expected_manifest_sha256=accepted.manifest_sha256,
    )
    replayed = stage_daily_shard(
        database_dsn=isolated_postgres_cluster["market_writer_dsn"],
        accepted=accepted,
        expected_manifest_sha256=accepted.manifest_sha256,
    )
    assert (inserted.outcome, inserted.bars, inserted.indices, inserted.macro) == (
        "INSERTED",
        31,
        2,
        2,
    )
    assert replayed.outcome == "NO_OP"
    assert replayed.provider_calls == 0
    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        counts = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM market_data_manifests WHERE manifest_kind = 'DAILY'),
              (SELECT count(*) FROM market_data_bars),
              (SELECT count(*) FROM market_data_indices),
              (SELECT count(*) FROM market_data_macro)
            """
        ).fetchone()
    assert counts == (1, 31, 2, 2)

    conflict_payload = {**accepted.payload, "manifestSha256": "f" * 64}
    conflict = AcceptedDailyShard(payload=conflict_payload, universe_rows=())
    with pytest.raises(psycopg.Error, match="NEEDS_HUMAN"):
        stage_daily_shard(
            database_dsn=isolated_postgres_cluster["market_writer_dsn"],
            accepted=conflict,
            expected_manifest_sha256="f" * 64,
        )


def test_db_commit_before_manifest_publish_recovers_as_no_op(
    tmp_path: Path, isolated_postgres_cluster: dict[str, str]
) -> None:
    packet, records = _packet_and_records()
    replay_root = _seal(tmp_path / "replay", records)
    run_root = tmp_path / "runs"
    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        connection.execute(
            """
            INSERT INTO market_data_manifests (
                manifest_sha256, manifest_kind, contract_id, session_date, as_of,
                generation, source_manifest_sha256, archive_sha256,
                calendar_revision, calendar_sha256, temporal_quality
            ) VALUES (%s, 'SEED', 'market-data-seed.v1', %s,
                      timestamptz '2026-08-18 00:00:00+00', 1, %s, %s, %s, %s,
                      'RECONSTRUCTED_FIXED_LAG')
            """,
            (
                _SHA,
                packet.previous_session_date,
                "1" * 64,
                "2" * 64,
                _CALENDAR_REVISION,
                "3" * 64,
            ),
        )

    class _CommitThenCrashSink(_Sink):
        def adopt(self, accepted: AcceptedDailyShard) -> str:
            stage_daily_shard(
                database_dsn=isolated_postgres_cluster["market_writer_dsn"],
                accepted=accepted,
                expected_manifest_sha256=accepted.manifest_sha256,
            )
            raise RuntimeError("simulated crash after DB commit")

    with pytest.raises(RuntimeError, match="simulated crash"):
        run_offline_daily(
            packet=packet,
            run_root=run_root,
            replay_factory=lambda: SealedDirectoryReplay(replay_root),
            sink=_CommitThenCrashSink(),
        )
    manifest_path = run_root / packet.packet_sha256 / "daily-shard.json"
    assert not manifest_path.exists()

    class _DatabaseSink(_Sink):
        outcomes: list[str]

        def __init__(self) -> None:
            super().__init__()
            self.outcomes = []

        def adopt(self, accepted: AcceptedDailyShard) -> str:
            outcome = stage_daily_shard(
                database_dsn=isolated_postgres_cluster["market_writer_dsn"],
                accepted=accepted,
                expected_manifest_sha256=accepted.manifest_sha256,
            ).outcome
            self.outcomes.append(outcome)
            return outcome

    sink = _DatabaseSink()
    recovered = run_offline_daily(
        packet=packet,
        run_root=run_root,
        replay_factory=lambda: SealedDirectoryReplay(replay_root),
        sink=sink,
    )

    assert recovered.status == "ACCEPTED"
    assert recovered.replay_reads == 0
    assert sink.outcomes == ["NO_OP"]
    assert manifest_path.is_file()
    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        count = connection.execute(
            "SELECT count(*) FROM market_data_manifests WHERE manifest_kind = 'DAILY'"
        ).fetchone()
    assert count == (1,)


def test_v76_rejects_disconnected_daily_chain(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        connection.execute(
            """
            INSERT INTO market_data_manifests (
                manifest_sha256, manifest_kind, contract_id, session_date, as_of,
                generation, source_manifest_sha256, archive_sha256,
                calendar_revision, calendar_sha256, temporal_quality
            ) VALUES (%s, 'SEED', 'market-data-seed.v1', date '2026-08-14',
                      timestamptz '2026-08-18 00:00:00+00', 1, %s, %s, %s, %s,
                      'RECONSTRUCTED_FIXED_LAG')
            """,
            (_SHA, "1" * 64, "2" * 64, _CALENDAR_REVISION, "3" * 64),
        )
        with pytest.raises(psycopg.Error, match="previous accepted market-data manifest"):
            connection.execute(
                """
                INSERT INTO market_data_manifests (
                    manifest_sha256, manifest_kind, contract_id, session_date, as_of,
                    generation, source_manifest_sha256, previous_manifest_sha256,
                    archive_sha256, receipt_set_sha256, calendar_revision,
                    calendar_sha256, temporal_quality
                ) VALUES (%s, 'DAILY', 'market-data-daily-shard.v1', date '2026-08-18',
                          timestamptz '2026-08-19 23:10:00+00', 1, %s, %s, %s, %s,
                          %s, %s, 'RECONSTRUCTED_FIXED_LAG')
                """,
                (
                    "4" * 64,
                    "5" * 64,
                    "f" * 64,
                    "4" * 64,
                    "6" * 64,
                    _CALENDAR_REVISION,
                    "7" * 64,
                ),
            )


def _packet_and_records(
    *, month_boundary: bool = False
) -> tuple[DailyReplayPacket, list[ReplayRecord]]:
    membership = (*tuple(f"{number:06d}" for number in range(1, 31)), "132030")
    membership_sha = canonical_json_sha256(list(membership))
    session_date = date(2026, 9, 1) if month_boundary else date(2026, 8, 18)
    previous_session_date = date(2026, 8, 31) if month_boundary else date(2026, 8, 14)
    as_of = evidence_clock_for_session(session_date)
    packet = DailyReplayPacket(
        session_date=session_date,
        as_of=as_of,
        checked_at=as_of.replace(minute=11),
        previous_session_date=previous_session_date,
        previous_accepted_manifest_sha256=_SHA,
        membership_month=session_date.strftime("%Y-%m"),
        membership=membership,
        previous_membership_sha256=membership_sha if not month_boundary else "b" * 64,
        month_boundary=month_boundary,
        generation=1,
        calendar_revision=_CALENDAR_REVISION,
        calendar_attestation_sha256=hashlib.sha256(_CALENDAR_REVISION.encode("ascii")).hexdigest(),
        expected_receipt_set_sha256="c" * 64,
    )
    records = [_record(operation, packet, membership) for operation in operation_ids(packet)]
    return _bind_receipts(packet, records), records


def _record(operation: str, packet: DailyReplayPacket, membership: tuple[str, ...]) -> ReplayRecord:
    if operation == "KRX_DAILY_01":
        payload: dict[str, object] = {
            "kind": "TRADING_EVIDENCE",
            "nonEmpty": True,
            "sessionDate": packet.session_date.isoformat(),
        }
    elif operation == "KRX_DAILY_02":
        payload = {
            "close": 3210.5,
            "indexId": "KOSPI",
            "kind": "INDEX",
            "sessionDate": packet.session_date.isoformat(),
        }
    elif operation == "KRX_DAILY_03":
        payload = {
            "close": 812.25,
            "indexId": "KOSDAQ",
            "kind": "INDEX",
            "sessionDate": packet.session_date.isoformat(),
        }
    elif operation == "KRX_MONTHLY_01":
        payload = {
            "kind": "UNIVERSE",
            "members": [
                {
                    "instrumentId": f"KR70000{rank:05d}",
                    "market": "KOSPI" if rank % 2 else "KOSDAQ",
                    "symbol": symbol,
                }
                for rank, symbol in enumerate(membership, start=1)
            ],
        }
    elif operation.startswith("KIS_DAILY_"):
        symbol = operation.removeprefix("KIS_DAILY_")
        value = 10_000 + membership.index(symbol)
        payload = {
            "close": value + 10,
            "high": value + 20,
            "kind": "BAR",
            "low": value - 20,
            "open": value,
            "sessionDate": packet.session_date.isoformat(),
            "symbol": symbol,
            "volume": 100_000 + membership.index(symbol),
        }
    elif operation.startswith("ECOS_DAILY_"):
        series = operation.removeprefix("ECOS_DAILY_")
        payload = {
            "availableAt": "2026-08-19T00:00:00Z",
            "kind": "MACRO",
            "observationDate": "2026-08-18",
            "seriesId": series,
            "value": 1300.5 if series.startswith("731") else 2.5,
        }
    else:
        payload = {"kind": "BOUNDED_MARKET_EVIDENCE", "operationId": operation}
    query_sha = canonical_json_sha256(
        {"operationId": operation, "sessionDate": packet.session_date.isoformat()}
    )
    return ReplayRecord(
        source_id=operation.split("_", maxsplit=1)[0],
        operation_id=operation,
        query_sha256=query_sha,
        content_sha256=canonical_json_sha256(payload),
        retrieved_at=packet.as_of,
        payload=payload,
    )


def _bind_receipts(packet: DailyReplayPacket, records: list[ReplayRecord]) -> DailyReplayPacket:
    return replace(
        packet,
        expected_receipt_set_sha256=canonical_json_sha256([record.receipt() for record in records]),
    )


def _seal(root: Path, records: list[ReplayRecord]) -> Path:
    for record in records:
        write_replay_record(root, record)
    return root


def _validate_contract(filename: str, instance: object) -> None:
    schema = json.loads((_ROOT / "contracts" / "schemas" / filename).read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(
        instance
    )
