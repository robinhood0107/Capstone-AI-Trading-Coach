from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.data.gdelt.collector import GdeltCollector
from app.data.gdelt.errors import GdeltAggregateError
from app.data.gdelt.policy import QueryDefinition, QueryRegistry
from app.data.gdelt.scoring import build_news_sentiment_summary
from app.data.gdelt.transport import FixtureResponse, FixtureTransport, validate_online_target

FIXTURE_ROOT = Path(__file__).with_name("fixtures")
WINDOW_START = datetime(2026, 7, 30, tzinfo=UTC)
WINDOW_END = datetime(2026, 7, 31, tzinfo=UTC)
OBSERVED_AT = datetime(2026, 7, 31, tzinfo=UTC)
RECEIVED_AT = datetime(2026, 7, 31, 0, 0, 1, tzinfo=UTC)
AVAILABLE_AT = datetime(2026, 7, 31, 0, 0, 2, tzinfo=UTC)


def _response(name: str) -> FixtureResponse:
    return FixtureResponse(
        content=(FIXTURE_ROOT / name).read_bytes(),
        content_type="application/json",
        redirected=False,
    )


def _query() -> QueryDefinition:
    return QueryDefinition(
        query_registry_id="global_semiconductor_stress_v1",
        aliases=("semiconductor", "chip supply"),
        entity_mapping_version="issuer_alias_v1",
        symbol="005930",
    )


def _collect(transport: FixtureTransport) -> dict[str, object]:
    return GdeltCollector(transport=transport).collect(
        query=_query(),
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        observed_at=OBSERVED_AT,
        received_at=RECEIVED_AT,
        available_at=AVAILABLE_AT,
    )


def test_fixture_collection_is_available_reproducible_and_network_free() -> None:
    transport = FixtureTransport(
        {
            "TIMELINE_TONE": _response("timeline_tone.valid.json"),
            "TIMELINE_VOL_RAW": _response("timeline_vol_raw.valid.json"),
        }
    )

    first = _collect(transport)
    second = _collect(
        FixtureTransport(
            {
                "TIMELINE_TONE": _response("timeline_tone.valid.json"),
                "TIMELINE_VOL_RAW": _response("timeline_vol_raw.valid.json"),
            }
        )
    )

    assert first == second
    assert first["status"] == "AVAILABLE"
    assert first["physicalAttemptCount"] == 0
    assert first["approvalPacketHash"] is None
    assert first["rawProviderDataStored"] is False
    assert first["articleMetadataStored"] is False
    assert first["decisionAuthority"] == "NONE"
    assert len(str(first["artifactHash"])) == 64
    assert transport.requests == ["TIMELINE_TONE", "TIMELINE_VOL_RAW"]

    schema = json.loads(
        (
            Path(__file__).resolve().parents[6]
            / "contracts/schemas/gdelt_news_tone_observation.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(first)


@pytest.mark.parametrize(
    "response",
    [
        FixtureResponse(content=b"{}", content_type="text/html", redirected=False),
        FixtureResponse(content=b"{}", content_type="application/json", redirected=True),
        FixtureResponse(
            content=b'{"timeline":[]}', content_type="application/json", redirected=False
        ),
    ],
)
def test_collection_abstains_without_fake_numeric_zero(response: FixtureResponse) -> None:
    observation = _collect(
        FixtureTransport(
            {
                "TIMELINE_TONE": response,
                "TIMELINE_VOL_RAW": _response("timeline_vol_raw.valid.json"),
            }
        )
    )

    assert observation["status"] == "ABSTAIN"
    assert "points" not in observation
    assert observation["physicalAttemptCount"] == 0
    assert all(key not in observation for key in ("sentimentScore", "articleCount"))


def test_first_fixture_failure_stops_remaining_mode() -> None:
    transport = FixtureTransport(
        {
            "TIMELINE_TONE": FixtureResponse(
                content=b"{}", content_type="text/html", redirected=False
            ),
            "TIMELINE_VOL_RAW": _response("timeline_vol_raw.valid.json"),
        }
    )

    observation = _collect(transport)

    assert observation["status"] == "ABSTAIN"
    assert transport.requests == ["TIMELINE_TONE"]


def test_news_summary_is_explanation_only_and_does_not_enter_decision_hash() -> None:
    observation = _collect(
        FixtureTransport(
            {
                "TIMELINE_TONE": _response("timeline_tone.valid.json"),
                "TIMELINE_VOL_RAW": _response("timeline_vol_raw.valid.json"),
            }
        )
    )

    summary = build_news_sentiment_summary(
        observation=observation,
        symbol="005930",
        as_of=AVAILABLE_AT,
        available_at=datetime(2026, 7, 31, 0, 0, 3, tzinfo=UTC),
    )

    assert summary["allowedUses"] == ["EXPLANATION_ONLY"]
    assert summary["riskDecisionHashIncluded"] is False
    assert summary["s5FeatureEligible"] is False
    assert summary["decisionAuthority"] == "NONE"

    schema = json.loads(
        (
            Path(__file__).resolve().parents[6]
            / "contracts/schemas/news_sentiment_summary.v2.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(
        summary
    )


def test_query_registry_rejects_alias_and_ticker_collisions() -> None:
    first = _query()
    second = QueryDefinition(
        query_registry_id="another_market_basket_v1",
        aliases=("SEMICONDUCTOR",),
        entity_mapping_version="issuer_alias_v1",
        symbol="005930",
    )

    with pytest.raises(GdeltAggregateError, match="MAPPING_AMBIGUOUS"):
        QueryRegistry((first, second))


@pytest.mark.parametrize(
    ("url", "redirects", "trust_env"),
    [
        ("http://api.gdeltproject.org/api/v2/doc/doc", False, False),
        ("https://127.0.0.1/api/v2/doc/doc", False, False),
        ("https://api.gdeltproject.org/other", False, False),
        ("https://api.gdeltproject.org/api/v2/doc/doc", True, False),
        ("https://api.gdeltproject.org/api/v2/doc/doc", False, True),
    ],
)
def test_online_target_is_fixed_origin_no_redirect_and_proxy_free(
    url: str,
    redirects: bool,
    trust_env: bool,
) -> None:
    with pytest.raises(GdeltAggregateError, match="PROVIDER_DISABLED"):
        validate_online_target(url=url, follow_redirects=redirects, trust_env=trust_env)


def test_online_target_policy_accepts_only_canonical_target_shape() -> None:
    assert (
        validate_online_target(
            url="https://api.gdeltproject.org/api/v2/doc/doc",
            follow_redirects=False,
            trust_env=False,
        )
        == "https://api.gdeltproject.org/api/v2/doc/doc"
    )
