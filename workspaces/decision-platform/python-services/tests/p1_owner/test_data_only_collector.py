from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, time
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from app.data._shared.canonical_json import canonical_json_sha256
from app.data.market_data.daily_runtime import (
    AcceptedDailyShard,
    DailyMarketDataError,
    ReplayRecord,
)
from app.p1_owner.data_only_collector import (
    DailyCollectorSettings,
    FixtureDailyCollectionTransport,
    P1DailyCollectorError,
    promote_staged_daily_collection,
    stage_fixture_daily_collection,
    stage_live_daily_collection,
)
from app.p1_owner.data_only_collector_live import LiveDailyCollectionTransport
from app.verification.network_guard import deny_outbound_network
from tests.data.market_data.test_daily_runtime import _packet_and_records

_KST = ZoneInfo("Asia/Seoul")


class _Sink:
    def __init__(self, expected_head: str) -> None:
        self.expected_head = expected_head
        self.accepted: list[AcceptedDailyShard] = []

    def preflight(self, packet: object) -> None:
        previous = getattr(packet, "previous_accepted_manifest_sha256")
        if previous != self.expected_head:
            raise DailyMarketDataError("previous manifest is not current")

    def adopt(self, accepted: AcceptedDailyShard) -> str:
        self.accepted.append(accepted)
        return "INSERTED"


def _collection_root(tmp_path: Path) -> Path:
    root = tmp_path / "collections"
    root.mkdir(mode=0o700)
    return root


def _scheduled_at(session_date: date, *, minute: int = 10) -> datetime:
    return datetime.combine(session_date, time(16, minute), tzinfo=_KST)


def _transport(records: list[ReplayRecord]) -> FixtureDailyCollectionTransport:
    return FixtureDailyCollectionTransport({record.operation_id: record for record in records})


class _LivePayloads:
    def __init__(self, records: list[ReplayRecord]) -> None:
        self.payloads = {record.operation_id: record.payload for record in records}

    def read(self, operation_id: str, packet: object) -> dict[str, object]:
        del packet
        return dict(self.payloads[operation_id])


def test_collector_is_default_off_and_before_schedule_makes_zero_calls(tmp_path: Path) -> None:
    packet, records = _packet_and_records()
    disabled = _transport(records)
    disabled_result = stage_fixture_daily_collection(
        packet=packet,
        observed_at=_scheduled_at(packet.session_date),
        collection_root=tmp_path / "not-created",
        transport=disabled,
    )
    assert disabled_result.status == "DISABLED"
    assert disabled_result.physical_calls == 0
    assert disabled.logical_calls == disabled.physical_calls == 0
    assert not (tmp_path / "not-created").exists()

    live = LiveDailyCollectionTransport(
        packet=packet,
        source=_LivePayloads(records),
        retrieved_at=packet.as_of,
        enabled=False,
    )
    with pytest.raises(P1DailyCollectorError, match="disabled"):
        live.collect("KRX_DAILY_01")
    assert live.logical_calls == 1
    assert live.physical_calls == 0

    early = _transport(records)
    early_result = stage_fixture_daily_collection(
        packet=packet,
        observed_at=_scheduled_at(packet.session_date, minute=9),
        collection_root=tmp_path / "also-not-created",
        transport=early,
        settings=DailyCollectorSettings(enabled=True),
    )
    assert early_result.status == "NOT_DUE"
    assert early.logical_calls == early.physical_calls == 0


def test_holiday_and_late_start_are_provider_free(tmp_path: Path) -> None:
    packet, records = _packet_and_records()
    holiday = replace(packet, session_date=date(2026, 8, 17))
    holiday_transport = _transport(records)
    result = stage_fixture_daily_collection(
        packet=holiday,
        observed_at=_scheduled_at(holiday.session_date),
        collection_root=tmp_path / "holiday-not-created",
        transport=holiday_transport,
        settings=DailyCollectorSettings(enabled=True),
    )
    assert result.status == "NO_NEW_SESSION"
    assert holiday_transport.logical_calls == holiday_transport.physical_calls == 0

    late_transport = _transport(records)
    late = stage_fixture_daily_collection(
        packet=packet,
        observed_at=datetime(2026, 8, 19, 8, 10, tzinfo=_KST),
        collection_root=tmp_path / "late-not-created",
        transport=late_transport,
        settings=DailyCollectorSettings(enabled=True),
    )
    assert late.status == "SKIPPED_LATE_START"
    assert late_transport.logical_calls == late_transport.physical_calls == 0


def test_exact38_fixture_collection_promotes_exact31_only_at_evidence_clock(
    tmp_path: Path,
) -> None:
    packet, records = _packet_and_records()
    collection_root = _collection_root(tmp_path)
    transport = _transport(records)

    with deny_outbound_network():
        staged = stage_fixture_daily_collection(
            packet=packet,
            observed_at=_scheduled_at(packet.session_date),
            collection_root=collection_root,
            transport=transport,
            settings=DailyCollectorSettings(enabled=True),
        )

    assert staged.status == "STAGED_COMPLETE"
    assert staged.logical_calls == 38
    assert staged.physical_calls == transport.physical_calls == 0
    assert staged.buy_candidate_allowed is False
    session_root = collection_root / packet.session_date.isoformat()
    manifest_path = session_root / "complete-manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_bytes())
    assert manifest["operationCount"] == 38
    assert manifest["evidenceClock"] == packet.as_of.isoformat()
    assert manifest["manifestSha256"] == staged.collection_manifest_sha256
    assert len((session_root / "collection-journal.jsonl").read_text().splitlines()) == 38

    sink = _Sink(packet.previous_accepted_manifest_sha256)
    promoted = promote_staged_daily_collection(
        packet=packet,
        collection_root=collection_root,
        run_root=tmp_path / "promotion",
        sink=sink,
    )
    assert promoted.status == "ACCEPTED"
    assert promoted.provider_physical_calls == 0
    accepted = cast(AcceptedDailyShard, promoted.accepted)
    assert len(cast(list[object], accepted.payload["bars"])) == 31
    assert len(sink.accepted) == 1
    assert (tmp_path / "promotion" / packet.packet_sha256 / "daily-shard.json").is_file()


def test_exact38_live_read_only_collection_is_bounded_and_promotes_offline(
    tmp_path: Path,
) -> None:
    packet, records = _packet_and_records()
    collection_root = _collection_root(tmp_path)
    live = LiveDailyCollectionTransport(
        packet=packet,
        source=_LivePayloads(records),
        retrieved_at=packet.as_of,
        enabled=True,
    )

    staged = stage_live_daily_collection(
        packet=packet,
        observed_at=_scheduled_at(packet.session_date),
        collection_root=collection_root,
        transport=live,
        settings=DailyCollectorSettings(enabled=True),
    )

    assert staged.status == "STAGED_COMPLETE"
    assert staged.logical_calls == staged.physical_calls == 38
    session_root = collection_root / packet.session_date.isoformat()
    manifest = json.loads((session_root / "complete-manifest.json").read_bytes())
    assert manifest["providerAuthority"] == "LIVE_READ_ONLY"
    assert manifest["providerPhysicalCalls"] == 38
    assert "fixturePhysicalCalls" not in manifest

    sink = _Sink(packet.previous_accepted_manifest_sha256)
    promoted = promote_staged_daily_collection(
        packet=packet,
        collection_root=collection_root,
        run_root=tmp_path / "live-promotion",
        sink=sink,
    )
    assert promoted.status == "ACCEPTED"
    assert promoted.provider_physical_calls == 0


def test_first_failure_stops_and_resume_never_recalls_successes(tmp_path: Path) -> None:
    packet, records = _packet_and_records()
    collection_root = _collection_root(tmp_path)
    failing = _transport(records)
    failing.failing_operation = "KIS_DAILY_000003"
    first = stage_fixture_daily_collection(
        packet=packet,
        observed_at=_scheduled_at(packet.session_date),
        collection_root=collection_root,
        transport=failing,
        settings=DailyCollectorSettings(enabled=True),
    )
    assert first.status == "EVIDENCE_GAP"
    assert first.logical_calls == 8
    assert failing.physical_calls == 0
    session_root = collection_root / packet.session_date.isoformat()
    assert not (session_root / "complete-manifest.json").exists()
    assert len(list((session_root / "records").glob("*.json"))) == 7

    resumed_transport = _transport(records)
    resumed = stage_fixture_daily_collection(
        packet=packet,
        observed_at=_scheduled_at(packet.session_date, minute=11),
        collection_root=collection_root,
        transport=resumed_transport,
        settings=DailyCollectorSettings(enabled=True),
    )
    assert resumed.status == "STAGED_COMPLETE"
    assert resumed.logical_calls == 31
    assert resumed_transport.physical_calls == 0
    assert len(list((session_root / "records").glob("*.json"))) == 38


def test_same_session_same_hash_is_noop_and_different_hash_halts(tmp_path: Path) -> None:
    packet, records = _packet_and_records()
    collection_root = _collection_root(tmp_path)
    first_transport = _transport(records)
    first = stage_fixture_daily_collection(
        packet=packet,
        observed_at=_scheduled_at(packet.session_date),
        collection_root=collection_root,
        transport=first_transport,
        settings=DailyCollectorSettings(enabled=True),
    )
    replay_transport = _transport(records)
    replay = stage_fixture_daily_collection(
        packet=packet,
        observed_at=_scheduled_at(packet.session_date, minute=12),
        collection_root=collection_root,
        transport=replay_transport,
        settings=DailyCollectorSettings(enabled=True),
    )
    assert first.status == "STAGED_COMPLETE"
    assert replay.status == "NO_OP"
    assert replay.collection_manifest_sha256 == first.collection_manifest_sha256
    assert replay_transport.logical_calls == replay_transport.physical_calls == 0

    conflicting = replace(packet, expected_receipt_set_sha256="f" * 64)
    conflict_transport = _transport(records)
    conflict = stage_fixture_daily_collection(
        packet=conflicting,
        observed_at=_scheduled_at(conflicting.session_date, minute=13),
        collection_root=collection_root,
        transport=conflict_transport,
        settings=DailyCollectorSettings(enabled=True),
    )
    assert conflict.status == "HALTED"
    assert conflict_transport.logical_calls == conflict_transport.physical_calls == 0


def test_missing_required_symbol_never_publishes_complete_manifest(tmp_path: Path) -> None:
    packet, records = _packet_and_records()
    target = next(record for record in records if record.operation_id == "KIS_DAILY_000001")
    invalid_payload = {**target.payload, "symbol": "999999"}
    invalid = replace(
        target,
        payload=invalid_payload,
        content_sha256=canonical_json_sha256(invalid_payload),
    )
    altered = [
        invalid if record.operation_id == target.operation_id else record for record in records
    ]
    collection_root = _collection_root(tmp_path)
    transport = _transport(altered)
    result = stage_fixture_daily_collection(
        packet=packet,
        observed_at=_scheduled_at(packet.session_date),
        collection_root=collection_root,
        transport=transport,
        settings=DailyCollectorSettings(enabled=True),
    )
    assert result.status == "EVIDENCE_GAP"
    assert result.logical_calls == 6
    assert result.buy_candidate_allowed is False
    assert transport.physical_calls == 0
    assert not (
        collection_root / packet.session_date.isoformat() / "complete-manifest.json"
    ).exists()


def test_receipt_set_drift_needs_human_without_complete_manifest(tmp_path: Path) -> None:
    packet, records = _packet_and_records()
    drifted_payload = {**records[-1].payload, "value": 9999.0}
    records[-1] = replace(
        records[-1],
        payload=drifted_payload,
        content_sha256=canonical_json_sha256(drifted_payload),
    )
    collection_root = _collection_root(tmp_path)
    transport = _transport(records)
    result = stage_fixture_daily_collection(
        packet=packet,
        observed_at=_scheduled_at(packet.session_date),
        collection_root=collection_root,
        transport=transport,
        settings=DailyCollectorSettings(enabled=True),
    )
    assert result.status == "NEEDS_HUMAN"
    assert result.logical_calls == 38
    assert transport.physical_calls == 0
    assert not (
        collection_root / packet.session_date.isoformat() / "complete-manifest.json"
    ).exists()


def test_nonzero_fixture_physical_count_is_rejected(tmp_path: Path) -> None:
    packet, records = _packet_and_records()
    transport = _transport(records)
    transport.physical_calls = 1
    with pytest.raises(P1DailyCollectorError, match="physical call"):
        stage_fixture_daily_collection(
            packet=packet,
            observed_at=_scheduled_at(packet.session_date),
            collection_root=_collection_root(tmp_path),
            transport=transport,
            settings=DailyCollectorSettings(enabled=True),
        )


def test_collection_root_symlink_is_rejected_before_fixture_calls(tmp_path: Path) -> None:
    packet, records = _packet_and_records()
    real_root = _collection_root(tmp_path)
    linked_root = tmp_path / "linked-collections"
    linked_root.symlink_to(real_root, target_is_directory=True)
    transport = _transport(records)
    with pytest.raises(P1DailyCollectorError, match="symlink"):
        stage_fixture_daily_collection(
            packet=packet,
            observed_at=_scheduled_at(packet.session_date),
            collection_root=linked_root,
            transport=transport,
            settings=DailyCollectorSettings(enabled=True),
        )
    assert transport.logical_calls == transport.physical_calls == 0


def test_module_has_no_live_provider_account_order_or_gdelt_transport() -> None:
    source = (Path(__file__).parents[2] / "app/p1_owner/data_only_collector.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "app.data.kis",
        "app.data.krx",
        "app.data.ecos",
        "app.data.gdelt",
        "app.brokerage",
        "httpx",
        "requests",
        "urllib",
        "socket",
        "accountId",
    ):
        assert forbidden not in source
    assert 'provider_authority: str = "FIXTURE_ONLY"' in source
    assert 'defaultEnabled": False' in source

    live_source = (
        Path(__file__).parents[2] / "app/p1_owner/data_only_collector_live.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "app.brokerage",
        "app.data.gdelt",
        "accountId",
        "httpx",
        "requests",
        "socket",
    ):
        assert forbidden not in live_source
