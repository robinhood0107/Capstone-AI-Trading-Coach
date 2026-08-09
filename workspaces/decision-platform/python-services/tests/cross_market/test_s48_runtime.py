from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.cross_market.s48_runtime import (
    S48AuthorizedProjection,
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


def test_projection_only_lanes_can_reuse_sanitized_authorized_projection_without_direct_call() -> None:
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
