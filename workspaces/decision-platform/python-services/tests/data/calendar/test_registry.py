from __future__ import annotations

from pathlib import Path

import pytest

from app.data.calendar.errors import RegistryValidationError
from app.data.calendar.registry import load_default_registry, load_registry


def test_default_registry_has_only_v1_sources_and_explicit_provenance() -> None:
    registry = load_default_registry()

    assert registry.version == "s1.6-v1"
    assert registry.verified_at == "2026-07-22"
    assert set(registry.sources) == {
        "xkrx-4.13.2",
        "kis-holiday-ctca0903r",
        "kasi-rest-de-info",
        "opendart-structured-events",
        "kis-ksd-dividend-hhkdb669102c0",
    }
    assert registry.sources["xkrx-4.13.2"].enabled_by_default is True
    assert registry.sources["kasi-rest-de-info"].network_ready is False
    assert all(source.tier in {1, 2, 3, 4} for source in registry.sources.values())

    seed_text = registry.seed_path.read_text(encoding="utf-8").lower()
    assert "api_key" not in seed_text
    assert "secret" not in seed_text
    assert "account" not in seed_text
    assert "credential" not in seed_text


@pytest.mark.parametrize(
    "mutation, expected",
    [
        ("remove_required", "missing"),
        ("unknown_field", "unknown"),
        ("duplicate_id", "duplicate"),
        ("unsafe_url", "https"),
        ("invalid_tier", "tier"),
        ("invalid_capability", "capability"),
        ("unsafe_enabled", "enabled"),
    ],
)
def test_registry_rejects_invalid_or_unsafe_seed(
    tmp_path: Path,
    mutation: str,
    expected: str,
) -> None:
    source = _valid_seed()
    if mutation == "remove_required":
        source = source.replace("    termsUrl: https://example.invalid/terms\n", "")
    elif mutation == "unknown_field":
        source += "    unexpected: value\n"
    elif mutation == "duplicate_id":
        source += _source_block()
    elif mutation == "unsafe_url":
        source = source.replace("https://example.invalid/docs", "http://example.invalid/docs")
    elif mutation == "invalid_tier":
        source = source.replace("    tier: 1", "    tier: 5")
    elif mutation == "invalid_capability":
        source = source.replace("MARKET_SESSION", "UNKNOWN_CAPABILITY")
    elif mutation == "unsafe_enabled":
        source = source.replace("    adoptionStatus: ACTIVE_PRIMARY", "    adoptionStatus: BLOCKED_LICENSE")

    seed = tmp_path / "registry.yaml"
    seed.write_text(source, encoding="utf-8")

    with pytest.raises(RegistryValidationError, match=expected):
        load_registry(seed)


def _valid_seed() -> str:
    return "version: s1.6-test\nverifiedAt: '2026-07-22'\nsources:\n" + _source_block()


def _source_block() -> str:
    return """  - sourceId: source-one
    sourceKind: OFFICIAL_API
    officialDocs:
      - https://example.invalid/docs
    termsUrl: https://example.invalid/terms
    verifiedAt: '2026-07-22'
    cost: FREE
    usageRestriction: INTERNAL_ONLY
    freshness: DAILY
    quotaScope: PROVIDER_ACCOUNT
    adoptionStatus: ACTIVE_PRIMARY
    activationGate: OFFLINE_FIXTURE
    projectUsage: TEST
    webSocketRole: NONE
    tier: 1
    originGroup: origin-one
    capabilities:
      - MARKET_SESSION
    networkReady: true
    enabledByDefault: true
    adapterVersion: '1'
    mappingVersion: '1'
"""
