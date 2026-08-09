from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.cross_market.foreign_news import (
    FOREIGN_NEWS_LANES,
    MODEL_CANDIDATES,
    ForeignNewsModelSelectionError,
    ForeignNewsSelectionMetrics,
    ForeignNewsSelectionRun,
    ForeignNewsSentimentError,
    ForeignNewsSentimentMaterializer,
    ForeignNewsSentimentRecord,
    ForeignNewsTransientLaneAggregate,
)
from app.data.gdelt.scoring import build_news_sentiment_summary


def test_gdelt_offline_reference_materializes_only_sanitized_lane_states() -> None:
    materializer = ForeignNewsSentimentMaterializer()

    record = materializer.from_gdelt_offline_reference(
        owner_user_id="usr_demo_user",
        symbol="005930",
        as_of=datetime(2026, 8, 9, 1, tzinfo=UTC),
        gdelt_summary=_available_gdelt_summary(),
    )

    assert record.status == "AVAILABLE"
    assert tuple(item.lane_id for item in record.lanes) == FOREIGN_NEWS_LANES
    assert [item.state for item in record.lanes] == [
        "NOT_ACTIVATED",
        "NOT_ACTIVATED",
        "NOT_ACTIVATED",
        "AVAILABLE",
    ]
    payload = record.to_public_payload()
    assert payload["rawProviderDataStored"] is False
    assert payload["articleMetadataStored"] is False
    assert "headline" not in str(payload).casefold()
    assert "contenthash" not in str(payload).casefold()
    assert "officialreleaselocator" not in str(payload).casefold()


def test_existing_gdelt_v2_aggregate_summary_is_reused_without_copying_legacy_fields() -> None:
    materializer = ForeignNewsSentimentMaterializer()

    record = materializer.from_gdelt_offline_reference(
        owner_user_id="usr_demo_user",
        symbol="005930",
        as_of=datetime(2026, 8, 9, 1, tzinfo=UTC),
        gdelt_summary=_available_gdelt_summary(),
    )

    assert record.status == "AVAILABLE"
    assert record.to_storage_payload() == {
        "allowedUses": ["EXPLANATION_ONLY"],
        "articleMetadataStored": False,
        "asOf": "2026-08-09T01:00:00Z",
        "contractId": "foreign-news-sentiment-v1",
        "decisionAuthority": "NONE",
        "lanes": [
            {"laneId": "FINNHUB_PERSONAL_LOCAL", "state": "NOT_ACTIVATED"},
            {"laneId": "SEC_OFFICIAL", "state": "NOT_ACTIVATED"},
            {"laneId": "FED_OFFICIAL", "state": "NOT_ACTIVATED"},
            {"laneId": "GDELT_OFFLINE_REFERENCE", "state": "AVAILABLE"},
        ],
        "rawProviderDataStored": False,
        "riskDecisionHashIncluded": False,
        "s5FeatureEligible": False,
        "schemaVersion": 1,
        "status": "AVAILABLE",
        "symbol": "005930",
    }


def test_transient_articles_cannot_be_persisted_or_returned_as_lane_payload() -> None:
    materializer = ForeignNewsSentimentMaterializer()
    unsafe = ForeignNewsTransientLaneAggregate(
        lane_id="SEC_OFFICIAL",
        state="AVAILABLE",
        content_hash="a" * 64,
        official_release_locator="sec-release:2026-08-09:001",
    )

    record = materializer.materialize(
        owner_user_id="usr_demo_user",
        symbol="005930",
        as_of=datetime(2026, 8, 9, 1, tzinfo=UTC),
        aggregates=(unsafe,),
    )

    assert record.status == "AVAILABLE"
    assert record.to_storage_payload()["lanes"] == [
        {"laneId": "FINNHUB_PERSONAL_LOCAL", "state": "NOT_ACTIVATED"},
        {"laneId": "SEC_OFFICIAL", "state": "AVAILABLE"},
        {"laneId": "FED_OFFICIAL", "state": "NOT_ACTIVATED"},
        {"laneId": "GDELT_OFFLINE_REFERENCE", "state": "NOT_ACTIVATED"},
    ]
    assert "contentHash" not in record.to_storage_payload()
    assert "officialReleaseLocator" not in record.to_storage_payload()

    with pytest.raises(ForeignNewsSentimentError, match="FOREIGN_NEWS_RAW_FIELD_FORBIDDEN"):
        ForeignNewsSentimentRecord.from_storage_payload(
            owner_user_id="usr_demo_user",
            payload={
                **record.to_storage_payload(),
                "headline": "must not be accepted",
            },
        )


def test_model_selection_uses_exact_validation_order_and_one_test_only() -> None:
    selection = ForeignNewsSelectionRun.from_validation(
        selection_id="fns_validation_0000001",
        selection_generation=1,
        results=(
            _metrics("PROSUSAI_FINBERT", macro_f1=0.84, ece=0.08, cpu_p95=16.0, footprint=100),
            _metrics("YIYANGHKUST_FINBERT_TONE", macro_f1=0.84, ece=0.06, cpu_p95=19.0, footprint=90),
            _metrics("LOUGHRAN_MCDONALD_BASELINE", macro_f1=0.79, ece=0.03, cpu_p95=1.0, footprint=1),
        ),
    )

    assert selection.candidate_models == MODEL_CANDIDATES
    assert selection.selection_status == "SELECTED_PENDING_TEST"
    assert selection.selected_model == "YIYANGHKUST_FINBERT_TONE"
    assert selection.test_evaluation_count == 0

    completed = selection.record_selected_model_test(passed=True)
    assert completed.selection_status == "TEST_EVALUATED"
    assert completed.test_evaluation_count == 1
    assert completed.test_target_model == "YIYANGHKUST_FINBERT_TONE"

    with pytest.raises(ForeignNewsModelSelectionError, match="FOREIGN_NEWS_TEST_ALREADY_EVALUATED"):
        completed.record_selected_model_test(passed=False)


def test_model_selection_abstains_without_test_when_no_candidate_meets_all_gates() -> None:
    selection = ForeignNewsSelectionRun.from_validation(
        selection_id="fns_validation_0000002",
        selection_generation=2,
        results=tuple(
            _metrics(candidate, macro_f1=0.79, ece=0.01, cpu_p95=1.0, footprint=1)
            for candidate in MODEL_CANDIDATES
        ),
    )

    assert selection.selection_status == "ABSTAIN"
    assert selection.abstain_reason == "NO_MODEL_MEETS_VALIDATION_GATE"
    assert selection.test_evaluation_count == 0
    assert selection.test_outcome == "NOT_RUN"


def test_materializer_rejects_duplicate_or_unknown_lane_without_storing_transient_content() -> None:
    materializer = ForeignNewsSentimentMaterializer()
    aggregate = ForeignNewsTransientLaneAggregate(
        lane_id="SEC_OFFICIAL",
        state="AVAILABLE",
        content_hash="b" * 64,
        official_release_locator="sec-release:2026-08-09:002",
    )

    with pytest.raises(ForeignNewsSentimentError, match="FOREIGN_NEWS_LANE_DUPLICATE"):
        materializer.materialize(
            owner_user_id="usr_demo_user",
            symbol="005930",
            as_of=datetime(2026, 8, 9, 1, tzinfo=UTC),
            aggregates=(aggregate, aggregate),
        )

    with pytest.raises(ForeignNewsSentimentError, match="FOREIGN_NEWS_LANE_INVALID"):
        ForeignNewsTransientLaneAggregate(
            lane_id="GDELT_HTTP",
            state="AVAILABLE",
            content_hash="c" * 64,
            official_release_locator=None,
        )


def _metrics(
    candidate_model: str,
    *,
    macro_f1: float,
    ece: float,
    cpu_p95: float,
    footprint: int,
) -> ForeignNewsSelectionMetrics:
    return ForeignNewsSelectionMetrics(
        candidate_model=candidate_model,
        class_recalls={"NEGATIVE": 0.80, "NEUTRAL": 0.81, "POSITIVE": 0.82},
        cpu_p95_millis=cpu_p95,
        critical_negation_number_unit_errors=0,
        ece=ece,
        footprint_bytes=footprint,
        macro_f1=macro_f1,
        neutral_f1=0.81,
    )


def _available_gdelt_summary() -> dict[str, object]:
    return build_news_sentiment_summary(
        observation={
            "artifactHash": "b" * 64,
            "attribution": {
                "citation": "The GDELT Project",
                "projectUrl": "https://www.gdeltproject.org/",
                "provider": "GDELT",
                "termsUrl": "https://www.gdeltproject.org/about.html",
            },
            "observationId": "gdelt_obs_market_20260809",
            "points": [
                {
                    "articleCount": 24,
                    "averageTone": 1.25,
                    "coverageRatio": 0.50,
                }
            ],
            "status": "AVAILABLE",
        },
        symbol="005930",
        as_of=datetime(2026, 8, 9, 1, tzinfo=UTC),
        available_at=datetime(2026, 8, 9, 1, tzinfo=UTC),
    )
