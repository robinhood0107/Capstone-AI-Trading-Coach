from __future__ import annotations

from datetime import UTC, datetime, timedelta

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


def test_semantically_approved_registry_passes_the_exact_registry_gate() -> None:
    assert verified_series(CANDIDATE_SERIES) == CANDIDATE_SERIES


@pytest.mark.parametrize(
    "updates",
    [
        {"series_id": "policy-rate-tampered"},
        {"stat_code": "0000000"},
        {"item_code1": "9999999"},
        {"cycle": "M"},
        {"name": "변경된 기준금리"},
        {"unit": "%"},
        {"registry_verified_at": _APPROVED_AT + timedelta(microseconds=1)},
        {"verified": False},
    ],
    ids=[
        "series-id",
        "stat-code",
        "item-code",
        "cycle",
        "name",
        "unit",
        "verified-at",
        "verified-flag",
    ],
)
def test_one_field_mismatch_never_passes_the_approved_registry_gate(
    updates: dict[str, object],
) -> None:
    mismatched = (
        CANDIDATE_SERIES[0].model_copy(update=updates),
        CANDIDATE_SERIES[1],
    )

    with pytest.raises(RegistryNotVerifiedError, match="registry_not_verified"):
        verified_series(mismatched)


@pytest.mark.parametrize(
    "updates",
    [
        {"registry_verified_at": None},
        {"name": None},
        {"unit": None},
    ],
    ids=["missing-timestamp", "missing-name", "missing-unit"],
)
def test_each_missing_approval_field_is_rejected_independently(
    updates: dict[str, object],
) -> None:
    incomplete = (
        CANDIDATE_SERIES[0].model_copy(update=updates),
        CANDIDATE_SERIES[1],
    )

    with pytest.raises(RegistryNotVerifiedError, match="registry_not_verified"):
        verified_series(incomplete)


def test_approved_registry_order_is_part_of_the_exact_contract() -> None:
    with pytest.raises(RegistryNotVerifiedError, match="registry_not_verified"):
        verified_series(tuple(reversed(CANDIDATE_SERIES)))
