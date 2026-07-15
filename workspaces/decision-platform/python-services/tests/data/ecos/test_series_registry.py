from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.data.ecos.series_registry import (
    CANDIDATE_SERIES,
    RegistryNotVerifiedError,
    verified_series,
)

_APPROVED_AT = datetime(2026, 7, 15, 6, 2, 19, 299552, tzinfo=UTC)


def test_source_controlled_registry_matches_semantically_approved_metadata() -> None:
    assert [
        (
            entry.series_id,
            entry.stat_code,
            entry.item_code1,
            entry.cycle,
            entry.name,
            entry.unit,
            entry.registry_verified_at,
            entry.verified,
        )
        for entry in CANDIDATE_SERIES
    ] == [
        (
            "policy-rate",
            "722Y001",
            "0101000",
            "D",
            "한국은행 기준금리",
            "연%",
            _APPROVED_AT,
            True,
        ),
        (
            "krw-usd-rate",
            "731Y001",
            "0000001",
            "D",
            "원/미국달러(매매기준율)",
            "원",
            _APPROVED_AT,
            True,
        ),
    ]


def test_semantically_approved_registry_unlocks_network_collection() -> None:
    assert verified_series(CANDIDATE_SERIES) == CANDIDATE_SERIES


def test_verified_flag_without_timestamp_evidence_still_blocks_collection() -> None:
    flag_only = tuple(
        entry.model_copy(
            update={
                "verified": True,
                "registry_verified_at": None,
                "name": None,
                "unit": None,
            }
        )
        for entry in CANDIDATE_SERIES
    )

    with pytest.raises(RegistryNotVerifiedError, match="registry_not_verified"):
        verified_series(flag_only)

    incomplete_name = tuple(
        entry.model_copy(
            update={
                "verified": True,
                "registry_verified_at": _APPROVED_AT,
                "name": None,
            }
        )
        for entry in CANDIDATE_SERIES
    )
    with pytest.raises(RegistryNotVerifiedError, match="registry_not_verified"):
        verified_series(incomplete_name)
