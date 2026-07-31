from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.cross_market.explanation_projection import (
    CrossMarketExplanationProjector,
    ExplanationProjectionError,
)
from app.cross_market.fixture_producer import SyntheticEodFixtureFactory


EVALUATED_AT = datetime(2026, 7, 31, 0, 30, tzinfo=UTC)


def test_projection_preserves_conflict_retraction_and_supersede_without_causal_upgrade() -> None:
    batch = SyntheticEodFixtureFactory().build(date(2026, 7, 31))
    causes = deepcopy(batch.cause_evidence)
    causes[0]["relation"] = "PRECEDES"
    causes[1]["retracted"] = True
    causes[1]["supersedesEvidenceId"] = "cause_gdelt_fixture_primary"

    result = CrossMarketExplanationProjector().project(
        symbol="005930",
        analyst_evidence=batch.analyst_evidence,
        cause_evidence=causes,
        gdelt_summary=_gdelt_summary(),
        evaluated_at=EVALUATED_AT,
    )

    assert len(result.causes) == 2
    preceding = next(cause for cause in result.causes if cause.relation == "PRECEDES")
    retracted = next(cause for cause in result.causes if cause.retracted)
    assert preceding.causal_claim is False
    assert retracted.supersedes_evidence_id == "cause_gdelt_fixture_primary"
    assert all(cause.contradiction_evidence_ids for cause in result.causes)
    assert result.decision_authority == "NONE"
    assert result.risk_decision_hash_included is False
    assert result.s5_feature_eligible is False
    assert result.rag_corpus_eligible is False
    assert result.provider_physical_calls == 0
    assert result.external_llm_calls == 0


def test_three_distinct_brokers_are_available_but_buy_level_directional_weight_is_zero() -> None:
    batch = SyntheticEodFixtureFactory().build(date(2026, 7, 31))

    result = CrossMarketExplanationProjector().project(
        symbol="005930",
        analyst_evidence=batch.analyst_evidence,
        cause_evidence=batch.cause_evidence,
        gdelt_summary=_gdelt_summary(),
        evaluated_at=EVALUATED_AT,
    )

    assert result.analyst.status == "AVAILABLE"
    assert result.analyst.distinct_broker_count == 3
    assert result.analyst.directional_weight == Decimal("0")
    assert len(result.analyst.revisions) == 3
    assert all(revision.rating == "BUY" for revision in result.analyst.revisions)


def test_fewer_than_three_distinct_brokers_is_insufficient_coverage_not_fake_zero() -> None:
    batch = SyntheticEodFixtureFactory().build(date(2026, 7, 31))

    result = CrossMarketExplanationProjector().project(
        symbol="005930",
        analyst_evidence=batch.analyst_evidence[:2],
        cause_evidence=batch.cause_evidence,
        gdelt_summary=_gdelt_summary(),
        evaluated_at=EVALUATED_AT,
    )

    assert result.analyst.status == "INSUFFICIENT_COVERAGE"
    assert result.analyst.distinct_broker_count == 2
    assert result.analyst.directional_weight is None


@pytest.mark.parametrize(
    ("classification", "relation"),
    [("CONFIRMED_FACT", "CO_MOVES_WITH"), ("MARKET_INTERPRETATION", "REPORTED_AS_CAUSE")],
)
def test_gdelt_never_creates_confirmed_fact_or_reported_as_cause(
    classification: str,
    relation: str,
) -> None:
    batch = SyntheticEodFixtureFactory().build(date(2026, 7, 31))
    invalid = deepcopy(batch.cause_evidence)
    invalid[0]["classification"] = classification
    invalid[0]["relation"] = relation

    with pytest.raises(ExplanationProjectionError, match="GDELT_CAUSALITY_FORBIDDEN"):
        CrossMarketExplanationProjector().project(
            symbol="005930",
            analyst_evidence=batch.analyst_evidence,
            cause_evidence=invalid,
            gdelt_summary=_gdelt_summary(),
            evaluated_at=EVALUATED_AT,
        )


def test_projection_is_byte_stable_under_input_reordering_and_changes_on_retraction() -> None:
    batch = SyntheticEodFixtureFactory().build(date(2026, 7, 31))
    projector = CrossMarketExplanationProjector()
    first = projector.project(
        symbol="005930",
        analyst_evidence=batch.analyst_evidence,
        cause_evidence=batch.cause_evidence,
        gdelt_summary=_gdelt_summary(),
        evaluated_at=EVALUATED_AT,
    )
    reordered = projector.project(
        symbol="005930",
        analyst_evidence=list(reversed(batch.analyst_evidence)),
        cause_evidence=list(reversed(batch.cause_evidence)),
        gdelt_summary=_gdelt_summary(),
        evaluated_at=EVALUATED_AT,
    )
    retracted = deepcopy(batch.cause_evidence)
    retracted[0]["retracted"] = True
    changed = projector.project(
        symbol="005930",
        analyst_evidence=batch.analyst_evidence,
        cause_evidence=retracted,
        gdelt_summary=_gdelt_summary(),
        evaluated_at=EVALUATED_AT,
    )

    assert first.canonical_bytes() == reordered.canonical_bytes()
    assert first.canonical_bytes() != changed.canonical_bytes()
    serialized = first.canonical_bytes().lower()
    assert b"rawtext" not in serialized
    assert b"quote" not in serialized


def _gdelt_summary() -> dict[str, object]:
    return {
        "allowedUses": ["EXPLANATION_ONLY"],
        "articleMetadataStored": False,
        "decisionAuthority": "NONE",
        "rawProviderDataStored": False,
        "riskDecisionHashIncluded": False,
        "s5FeatureEligible": False,
        "status": "AVAILABLE",
    }
