from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from app.data.gdelt.approval import validate_approval_packet
from app.data.gdelt.errors import GdeltAggregateError
from app.data.gdelt.policy import QueryDefinition


def _query() -> QueryDefinition:
    return QueryDefinition(
        query_registry_id="global_semiconductor_stress_v1",
        aliases=("semiconductor", "chip supply"),
        entity_mapping_version="issuer_alias_v1",
        symbol="005930",
    )


def _packet() -> dict[str, object]:
    query = _query()
    return {
        "schemaVersion": "1",
        "headSha": "1" * 40,
        "origin": "https://api.gdeltproject.org",
        "path": "/api/v2/doc/doc",
        "format": "json",
        "modes": ["TIMELINE_TONE", "TIMELINE_VOL_RAW"],
        "queryRegistryId": query.query_registry_id,
        "queryDefinitionHash": query.definition_hash,
        "windowStart": "2026-07-30T00:00:00Z",
        "windowEnd": "2026-07-31T00:00:00Z",
        "physicalCap": 1,
        "retryCount": 0,
        "persistRaw": False,
        "attribution": "The GDELT Project",
        "operatorPurpose": "bounded aggregate quality study",
        "expiresAt": "2026-08-02T00:00:00Z",
    }


def _encode(packet: dict[str, object]) -> bytes:
    return json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()


def test_approval_packet_binds_exact_query_window_and_zero_retry() -> None:
    content = _encode(_packet())
    validated = validate_approval_packet(
        content=content,
        expected_sha256=hashlib.sha256(content).hexdigest(),
        expected_head_sha="1" * 40,
        query=_query(),
        now=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert validated.physical_cap == 1
    assert validated.retry_count == 0
    assert validated.persist_raw is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("origin", "https://127.0.0.1"),
        ("path", "/other"),
        ("format", "html"),
        ("modes", ["ArticleList"]),
        ("queryDefinitionHash", "2" * 64),
        ("physicalCap", 2),
        ("retryCount", 1),
        ("persistRaw", True),
        ("attribution", "unknown"),
        ("expiresAt", "2026-07-31T23:59:59Z"),
    ],
)
def test_approval_packet_rejects_policy_drift(field: str, value: object) -> None:
    packet = deepcopy(_packet())
    packet[field] = value
    content = _encode(packet)

    with pytest.raises(GdeltAggregateError, match="PROVIDER_DISABLED"):
        validate_approval_packet(
            content=content,
            expected_sha256=hashlib.sha256(content).hexdigest(),
            expected_head_sha="1" * 40,
            query=_query(),
            now=datetime(2026, 8, 1, tzinfo=UTC),
        )


def test_approval_packet_rejects_hash_extra_field_and_noncanonical_json() -> None:
    packet = _packet()
    packet["unexpected"] = True
    content = json.dumps(packet, indent=2).encode()

    with pytest.raises(GdeltAggregateError, match="PROVIDER_DISABLED"):
        validate_approval_packet(
            content=content,
            expected_sha256="0" * 64,
            expected_head_sha="1" * 40,
            query=_query(),
            now=datetime(2026, 8, 1, tzinfo=UTC),
        )
