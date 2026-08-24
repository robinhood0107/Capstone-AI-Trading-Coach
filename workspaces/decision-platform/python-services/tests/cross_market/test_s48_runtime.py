from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.cross_market.core6_probe import Core6ProbeReceipt, core6_endpoint_set_identity_hash
from app.cross_market.s48_runtime import (
    S48AuthorizedProjection,
    S48DirectProbeProjection,
    S48RuntimeError,
    S48RuntimeInMemoryRepository,
    S48RuntimeMaterializer,
)


def test_runtime_materializes_exact_nine_fixture_first_lanes_without_provider_calls() -> None:
    batch = S48RuntimeMaterializer().materialize(
        evaluated_at=datetime(2026, 8, 9, 1, tzinfo=UTC),
    )

    assert [(item.source_family, item.source_id) for item in batch.lanes] == [
        ("KIS", "S48_CORE6_KIS"),
        ("OPENDART", "S48_CORE6_OPENDART"),
        ("SEC_EDGAR", "S48_CORE6_SEC_EDGAR"),
        ("KRX", "S48_CORE6_KRX"),
        ("KOFIA", "S48_CORE6_KOFIA"),
        ("ECOS", "S48_CORE6_ECOS"),
        ("FINNHUB_OPTIONAL3", "S48_OPTIONAL3_FINNHUB"),
        ("TWELVE_DATA", "S48_OPTIONAL3_TWELVE_DATA"),
        ("MASSIVE", "S48_OPTIONAL3_MASSIVE"),
    ]
    assert [(item.status, item.reason) for item in batch.lanes] == [
        ("ABSTAIN", "APPROVAL_PACKET_REQUIRED"),
        ("ABSTAIN", "REUSE_AUTHORIZED_PROJECTION_NOT_AVAILABLE"),
        ("ABSTAIN", "APPROVAL_PACKET_REQUIRED"),
        ("ABSTAIN", "APPROVAL_PACKET_REQUIRED"),
        ("BLOCKED", "BLOCKED_NO_CREDENTIAL_OR_APPROVAL"),
        ("ABSTAIN", "REUSE_AUTHORIZED_PROJECTION_NOT_AVAILABLE"),
        ("BLOCKED", "BLOCKED_NO_CREDENTIAL_OR_ENTITLEMENT"),
        ("BLOCKED", "BLOCKED_NO_CREDENTIAL_OR_ENTITLEMENT"),
        ("BLOCKED", "BLOCKED_NO_CREDENTIAL_OR_ENTITLEMENT"),
    ]
    assert batch.provider_physical_calls == 0
    assert batch.retry_count == 0

    for record in batch.writer_records():
        assert record["decisionAuthority"] == "NONE"
        assert record["riskSignalOrderAuthority"] == "NONE"
        assert record["orderAuthority"] == "NONE"
        assert record["rawProviderDataStored"] is False
        assert record["providerPhysicalCalls"] == 0
        assert "credential" not in record
        assert "rawResponse" not in record
        assert "query" not in record


def test_projection_only_lanes_can_reuse_sanitized_authorized_projection_without_direct_call() -> (
    None
):
    batch = S48RuntimeMaterializer().materialize(
        evaluated_at=datetime(2026, 8, 9, 1, tzinfo=UTC),
        authorized_projections=(
            S48AuthorizedProjection(
                source_family="OPENDART",
                projection_hash="a" * 64,
            ),
            S48AuthorizedProjection(
                source_family="ECOS",
                projection_hash="b" * 64,
            ),
        ),
    )

    by_family = {item.source_family: item for item in batch.lanes}
    assert by_family["OPENDART"].status == "AVAILABLE"
    assert by_family["OPENDART"].reason == "AUTHORIZED_PROJECTION_AVAILABLE"
    assert by_family["OPENDART"].projection_hash == "a" * 64
    assert by_family["ECOS"].status == "AVAILABLE"
    assert by_family["ECOS"].projection_hash == "b" * 64
    assert batch.provider_physical_calls == 0

    with pytest.raises(S48RuntimeError, match="S48_DIRECT_PROJECTION_REUSE_FORBIDDEN"):
        S48RuntimeMaterializer().materialize(
            evaluated_at=datetime(2026, 8, 9, 1, tzinfo=UTC),
            authorized_projections=(
                S48AuthorizedProjection(
                    source_family="KIS",
                    projection_hash="c" * 64,
                ),
            ),
        )


def test_runtime_batch_is_atomic_replay_safe_and_rejects_outbound_materialization() -> None:
    batch = S48RuntimeMaterializer().materialize(
        evaluated_at=datetime(2026, 8, 9, 1, tzinfo=UTC),
    )
    repository = S48RuntimeInMemoryRepository()

    inserted = repository.append_batch(batch)
    replay = repository.append_batch(batch)

    assert inserted.inserted == 9
    assert inserted.replayed == 0
    assert replay.inserted == 0
    assert replay.replayed == 9

    with pytest.raises(S48RuntimeError, match="S48_RUNTIME_PROVIDER_CALLS_FORBIDDEN"):
        S48RuntimeMaterializer().materialize(
            evaluated_at=datetime(2026, 8, 9, 1, tzinfo=UTC),
            provider_physical_calls=1,
        )


def test_complete_core6_receipt_sets_make_only_kis_sec_and_krx_available_without_runtime_outbound() -> (
    None
):
    projections = tuple(
        S48DirectProbeProjection.from_core6_receipt(_successful_receipt(operation, index))
        for index, operation in enumerate(
            (
                "KIS_CURRENT_PRICE",
                "SEC_EDGAR_SUBMISSIONS",
                "SEC_EDGAR_COMPANYFACTS",
                "KRX_KOSPI_DAILY",
                "KRX_KOSDAQ_DAILY",
            )
        )
    )

    batch = S48RuntimeMaterializer().materialize(
        evaluated_at=datetime(2026, 8, 9, 1, tzinfo=UTC),
        direct_probe_projections=projections,
    )

    by_family = {item.source_family: item for item in batch.lanes}
    for family in ("KIS", "SEC_EDGAR", "KRX"):
        assert by_family[family].status == "AVAILABLE"
        assert by_family[family].reason == "COMPLETE_DIRECT_PROBE_SET_AVAILABLE"
        assert by_family[family].projection_hash is not None
    assert by_family["KOFIA"].status == "BLOCKED"
    assert batch.provider_physical_calls == 0


def test_incomplete_or_duplicate_core6_receipt_proofs_fail_closed() -> None:
    projection = S48DirectProbeProjection.from_core6_receipt(
        _successful_receipt("SEC_EDGAR_SUBMISSIONS", 1)
    )

    batch = S48RuntimeMaterializer().materialize(
        evaluated_at=datetime(2026, 8, 9, 1, tzinfo=UTC),
        direct_probe_projections=(projection,),
    )

    by_family = {item.source_family: item for item in batch.lanes}
    assert by_family["SEC_EDGAR"].status == "ABSTAIN"
    assert by_family["SEC_EDGAR"].reason == "DIRECT_PROBE_RECEIPT_SET_INCOMPLETE"

    with pytest.raises(S48RuntimeError, match="S48_RUNTIME_DIRECT_RECEIPT_DUPLICATE"):
        S48RuntimeMaterializer().materialize(
            evaluated_at=datetime(2026, 8, 9, 1, tzinfo=UTC),
            direct_probe_projections=(projection, projection),
        )


def _successful_receipt(operation: str, index: int) -> Core6ProbeReceipt:
    source_family, source_id = {
        "KIS_CURRENT_PRICE": ("KIS", "S48_CORE6_KIS"),
        "SEC_EDGAR_SUBMISSIONS": ("SEC_EDGAR", "S48_CORE6_SEC_EDGAR"),
        "SEC_EDGAR_COMPANYFACTS": ("SEC_EDGAR", "S48_CORE6_SEC_EDGAR"),
        "KRX_KOSPI_DAILY": ("KRX", "S48_CORE6_KRX"),
        "KRX_KOSDAQ_DAILY": ("KRX", "S48_CORE6_KRX"),
    }[operation]
    now = datetime(2026, 8, 9, 1, tzinfo=UTC)
    letter = chr(ord("a") + index)
    return Core6ProbeReceipt(
        approval_id_hash=letter * 64,
        approval_packet_sha256=letter * 64,
        completed_at=now,
        endpoint_set_identity_hash=core6_endpoint_set_identity_hash(source_family),
        logical_call_count=1,
        operation=operation,
        outcome="SUCCESS",
        physical_call_count=1,
        projection_hash=letter * 64,
        provider_family=source_family,
        provider_status_class="HTTP_2XX",
        request_plan_digest=letter * 64,
        source_id=source_id,
        started_at=now,
    )
