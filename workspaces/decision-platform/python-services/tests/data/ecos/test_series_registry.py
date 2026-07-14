from __future__ import annotations

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
