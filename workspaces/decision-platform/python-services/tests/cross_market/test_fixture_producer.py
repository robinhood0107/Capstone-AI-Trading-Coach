from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest

from app.cross_market.fixture_producer import (
    CrossMarketFixtureProducer,
    InMemoryAppendOnlyCrossMarketRepository,
    PayloadConflictError,
    SyntheticEodFixtureFactory,
)


def test_factory_materializes_exact_disabled_inventory_and_completed_session_fixture() -> None:
    batch = SyntheticEodFixtureFactory().build(date(2026, 7, 31))

    assert len(batch.entitlements) == 19
    assert [item["category"] for item in batch.entitlements].count("OVERSEAS_LEAD") == 4
    assert [item["category"] for item in batch.entitlements].count("DOMESTIC_AMPLIFICATION") == 11
    assert [item["category"] for item in batch.entitlements].count("ANALYST") == 3
    assert [item["category"] for item in batch.entitlements].count("NEWS_AGGREGATE") == 1
    assert all(item["activationStatus"] == "CANDIDATE_DISABLED" for item in batch.entitlements)
    assert all(item["providerCallsAllowed"] is False for item in batch.entitlements)
    assert len(batch.observations) == 18 * 253
    assert len({item["sessionDate"] for item in batch.observations}) == 253
    assert len(batch.analyst_evidence) == 3
    assert len(batch.cause_evidence) == 2
    assert batch.provider_physical_calls == 0
    assert batch.external_llm_calls == 0


def test_fixture_producer_is_atomic_replay_safe_and_stops_on_conflict() -> None:
    batch = SyntheticEodFixtureFactory().build(date(2026, 7, 31))
    repository = InMemoryAppendOnlyCrossMarketRepository()
    producer = CrossMarketFixtureProducer(repository)

    inserted = producer.materialize(batch)
    replay = producer.materialize(batch)

    assert inserted.inserted == batch.record_count
    assert inserted.replayed == 0
    assert replay.inserted == 0
    assert replay.replayed == batch.record_count
    assert repository.record_count == batch.record_count

    mutated = deepcopy(batch)
    mutated.observations[0]["payloadHash"] = "f" * 64
    with pytest.raises(PayloadConflictError):
        producer.materialize(mutated)
    assert repository.record_count == batch.record_count


def test_fixture_payload_has_no_raw_provider_pdf_news_account_or_credential_fields() -> None:
    batch = SyntheticEodFixtureFactory().build(date(2026, 7, 31))
    serialized = batch.canonical_bytes().decode("utf-8").lower()

    for forbidden in (
        "rawbody",
        "articleurl",
        "articletitle",
        "pdfcontent",
        "accountnumber",
        "credential",
        "apikey",
        "accesstoken",
    ):
        assert forbidden not in serialized
    assert all(item["rawTextStored"] is False for item in batch.analyst_evidence)


def test_producer_rejects_any_nonzero_outbound_counter_before_repository_write() -> None:
    batch = SyntheticEodFixtureFactory().build(date(2026, 7, 31))
    batch.provider_physical_calls = 1
    repository = InMemoryAppendOnlyCrossMarketRepository()

    with pytest.raises(ValueError, match="provider physical calls"):
        CrossMarketFixtureProducer(repository).materialize(batch)
    assert repository.record_count == 0
