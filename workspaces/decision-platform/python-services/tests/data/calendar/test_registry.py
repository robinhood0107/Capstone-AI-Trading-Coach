from __future__ import annotations

from pathlib import Path

import pytest

from app.data.calendar.errors import RegistryValidationError
from app.data.calendar.registry import load_default_registry, load_registry


def test_default_registry_matches_the_frozen_exact_v1_schema() -> None:
    registry = load_default_registry()

    assert registry.schema_version == "1"
    assert registry.registry_version == "s1.6-v1"
    assert registry.generated_at.isoformat() == "2026-07-22T00:00:00+00:00"
    assert set(registry.sources) == {
        "xkrx-4.13.2",
        "kis-holiday-ctca0903r",
        "kasi-rest-de-info",
        "opendart-structured-events",
        "kis-ksd-dividend-hhkdb669102c0",
    }
    xkrx = registry.sources["xkrx-4.13.2"]
    assert xkrx.provider == "EXCHANGE_CALENDARS"
    assert xkrx.category == "SESSION"
    assert xkrx.license_class == "OFFICIAL_NO_FEE"
    assert xkrx.reliability_tier == 2
    assert xkrx.origin.kind == "OFFLINE"
    assert xkrx.retention.mode == "EPHEMERAL_ONLY"
    assert xkrx.enabled_by_default is True

    opendart = registry.sources["opendart-structured-events"]
    assert opendart.origin.base_url == "https://opendart.fss.or.kr"
    assert opendart.retention.mode == "OPERATOR_REQUIRED"
    assert opendart.retention.days is None
    assert opendart.retention.owner is None
    assert opendart.enabled_by_default is False

    seed_text = registry.seed_path.read_text(encoding="utf-8").lower()
    for forbidden in ("api_key", "secret", "account", "credential", "configured"):
        assert forbidden not in seed_text


@pytest.mark.parametrize(
    "mutation, expected",
    [
        ("remove_required", "missing"),
        ("unsupported_schema", "schema"),
        ("unknown_field", "unknown"),
        ("duplicate_id", "duplicate"),
        ("unsafe_url", "https"),
        ("invalid_license", "license"),
        ("invalid_tier", "tier"),
        ("invalid_capability", "capability"),
        ("unsafe_enabled", "enabled"),
        ("incomplete_retention", "retention"),
        ("enabled_without_retention", "retention"),
        ("invalid_provenance", "provenance"),
    ],
)
def test_registry_rejects_invalid_or_unsafe_seed(
    tmp_path: Path,
    mutation: str,
    expected: str,
) -> None:
    source = _valid_seed()
    if mutation == "remove_required":
        source = source.replace("    mappingVersion: '1'\n", "")
    elif mutation == "unsupported_schema":
        source = source.replace("schemaVersion: '1'", "schemaVersion: '2'")
    elif mutation == "unknown_field":
        source += "    unexpected: value\n"
    elif mutation == "duplicate_id":
        source += _source_block()
    elif mutation == "unsafe_url":
        source = source.replace("https://example.invalid", "http://example.invalid")
    elif mutation == "invalid_license":
        source = source.replace("    licenseClass: OFFICIAL_NO_FEE", "    licenseClass: UNKNOWN")
    elif mutation == "invalid_tier":
        source = source.replace("    reliabilityTier: 1", "    reliabilityTier: 5")
    elif mutation == "invalid_capability":
        source = source.replace("MARKET_SESSION", "UNKNOWN_CAPABILITY")
    elif mutation == "unsafe_enabled":
        source = source.replace("    licenseClass: OFFICIAL_NO_FEE", "    licenseClass: UNSAFE_OR_EXCLUDE")
    elif mutation == "incomplete_retention":
        source = source.replace("      days: 30\n      owner: fixture-owner\n", "")
    elif mutation == "enabled_without_retention":
        source = source.replace(
            "      mode: PERSISTENT\n      days: 30\n      owner: fixture-owner",
            "      mode: OPERATOR_REQUIRED",
        )
    elif mutation == "invalid_provenance":
        source = source.replace("      verifiedAt: '2026-07-22'", "      verifiedAt: yesterday")

    seed = tmp_path / "registry.yaml"
    seed.write_text(source, encoding="utf-8")

    with pytest.raises(RegistryValidationError, match=expected):
        load_registry(seed)


def _valid_seed() -> str:
    return (
        "schemaVersion: '1'\n"
        "registryVersion: s1.6-test\n"
        "generatedAt: '2026-07-22T00:00:00Z'\n"
        "sources:\n"
        + _source_block()
    )


def _source_block() -> str:
    return """  - sourceId: source-one
    provider: FIXTURE
    category: SESSION
    licenseClass: OFFICIAL_NO_FEE
    reliabilityTier: 1
    capabilities:
      - MARKET_SESSION
    originGroup: origin-one
    origin:
      kind: HTTPS
      baseUrl: https://example.invalid
    mappingVersion: '1'
    networkReady: true
    enabledByDefault: true
    retention:
      mode: PERSISTENT
      days: 30
      owner: fixture-owner
    provenance:
      verifiedAt: '2026-07-22'
      sourceVersion: fixture-v1
      evidenceUrl: https://example.invalid/docs
      attribution: fixture
"""
