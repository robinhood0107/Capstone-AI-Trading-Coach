from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.data.ecos.series_registry import (
    CANDIDATE_SERIES,
    RegistryNotVerifiedError,
    verified_series,
)


def test_candidate_registry_is_provisional_and_bounded() -> None:
    assert [(entry.stat_code, entry.item_code1, entry.cycle) for entry in CANDIDATE_SERIES] == [
        ("722Y001", "0101000", "D"),
        ("731Y001", "0000001", "D"),
    ]
    assert all(entry.verified is False for entry in CANDIDATE_SERIES)


def test_provisional_registry_blocks_network_collection() -> None:
    with pytest.raises(RegistryNotVerifiedError, match="registry_not_verified"):
        verified_series(CANDIDATE_SERIES)


def test_verified_flag_without_timestamp_evidence_still_blocks_collection() -> None:
    flag_only = tuple(entry.model_copy(update={"verified": True}) for entry in CANDIDATE_SERIES)

    with pytest.raises(RegistryNotVerifiedError, match="registry_not_verified"):
        verified_series(flag_only)

    verified_at = datetime(2026, 7, 14, tzinfo=UTC)
    evidenced = tuple(
        entry.model_copy(
            update={
                "verified": True,
                "registry_verified_at": verified_at,
                "name": f"synthetic-{entry.series_id}",
                "unit": "synthetic-unit",
            }
        )
        for entry in CANDIDATE_SERIES
    )
    assert verified_series(evidenced) == evidenced
