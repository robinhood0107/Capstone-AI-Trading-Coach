from __future__ import annotations

from copy import deepcopy

import pytest

from app.data.ecos.storage import (
    ECOS_SNAPSHOT_MAX_BYTES,
    ECOSSnapshotStorageError,
    serialize_ecos_snapshot,
)


def _snapshot() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "source": "ecos",
        "asOf": "2026-07-14",
        "retrievedAt": "2026-07-14T00:00:00Z",
        "registryVersion": "ecos-v1",
        "registryVerifiedAt": "2026-07-14T00:00:00Z",
        "series": [
            {
                "seriesId": "policy-rate",
                "statCode": "722Y001",
                "itemCode1": "0101000",
                "cycle": "D",
                "name": "synthetic policy rate",
                "unit": "%",
                "requestedFrom": "20260701",
                "requestedTo": "20260714",
                "status": "complete",
                "observations": [{"time": "20260714", "value": "2.5"}],
            },
            {
                "seriesId": "krw-usd-rate",
                "statCode": "731Y001",
                "itemCode1": "0000001",
                "cycle": "D",
                "name": "synthetic KRW/USD rate",
                "unit": "KRW",
                "requestedFrom": "20260701",
                "requestedTo": "20260714",
                "status": "empty",
                "observations": [],
            },
        ],
        "partial": False,
        "coverage": "complete",
    }


def test_snapshot_serialization_is_deterministic_sanitized_and_newline_terminated() -> None:
    first = _snapshot()
    second = dict(reversed(list(first.items())))

    encoded = serialize_ecos_snapshot(first)

    assert encoded == serialize_ecos_snapshot(second)
    assert encoded.endswith(b"\n")
    assert len(encoded) <= ECOS_SNAPSHOT_MAX_BYTES == 2 * 1024 * 1024
    lowered = encoded.lower()
    assert b"credential" not in lowered
    assert b"authorization" not in lowered
    assert b"rawbody" not in lowered


@pytest.mark.parametrize("forbidden", ["credential", "requestUrl", "authorization", "rawBody"])
def test_forbidden_fields_are_rejected_recursively(forbidden: str) -> None:
    payload = deepcopy(_snapshot())
    payload["series"][0][forbidden] = "synthetic-secret"  # type: ignore[index]

    with pytest.raises(ECOSSnapshotStorageError, match="forbidden"):
        serialize_ecos_snapshot(payload)


def test_snapshot_over_two_mib_is_rejected_before_publish() -> None:
    payload = _snapshot()
    payload["padding"] = "x" * ECOS_SNAPSHOT_MAX_BYTES

    with pytest.raises(ECOSSnapshotStorageError, match="size"):
        serialize_ecos_snapshot(payload)


def test_snapshot_contract_is_validated_before_serialization() -> None:
    payload = _snapshot()
    payload["series"] = payload["series"][:1]  # type: ignore[index]

    with pytest.raises(ECOSSnapshotStorageError, match="contract"):
        serialize_ecos_snapshot(payload)
