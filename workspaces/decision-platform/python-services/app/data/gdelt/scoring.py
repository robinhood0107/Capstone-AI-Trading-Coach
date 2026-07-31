from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_EVEN
from typing import cast

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.gdelt.errors import GdeltAggregateError

_QUANTUM = Decimal("0.00000001")


def build_news_sentiment_summary(
    *,
    observation: dict[str, object],
    symbol: str,
    as_of: datetime,
    available_at: datetime,
) -> dict[str, object]:
    """aggregate observation을 설명 전용 summary v2로 결정적으로 투영한다.

    출력은 RiskDecision/hash와 S5 feature에 포함되지 않으며 production threshold나 주문 권한을
    만들지 않는다.
    """

    as_of_utc = _utc(as_of)
    available_utc = _utc(available_at)
    if as_of_utc > available_utc:
        raise GdeltAggregateError("INVALID_RESPONSE", "summary availability is invalid")
    base: dict[str, object] = {
        "schemaVersion": "2",
        "artifactId": f"news_sum_{symbol}_{as_of_utc.strftime('%Y%m%d')}",
        "symbol": symbol,
        "asOf": _format_utc(as_of_utc),
        "availableAt": _format_utc(available_utc),
        "producer": "NEWS_SENTIMENT_AGGREGATOR",
        "sourceWorkspace": "decision-platform",
        "decisionAuthority": "NONE",
        "allowedUses": ["EXPLANATION_ONLY"],
        "riskDecisionHashIncluded": False,
        "s5FeatureEligible": False,
        "rawProviderDataStored": False,
        "articleMetadataStored": False,
        "attribution": dict(cast(dict[str, object], observation["attribution"])),
        "sourceObservationRefs": [
            {
                "observationId": observation["observationId"],
                "artifactHash": observation["artifactHash"],
            }
        ],
    }
    if observation.get("status") != "AVAILABLE":
        return _with_hash(
            {
                **base,
                "status": "ABSTAIN",
                "qualityStatus": "INCOMPLETE",
                "abstainReason": "INPUT_INCOMPLETE",
            }
        )
    points = cast(list[dict[str, object]], observation.get("points"))
    if not points:
        raise GdeltAggregateError("INCOMPLETE_SOURCE", "available observation has no points")
    counts = [cast(int, point["articleCount"]) for point in points]
    tones = [Decimal(str(point["averageTone"])) for point in points]
    coverages = [Decimal(str(point["coverageRatio"])) for point in points]
    total_count = sum(counts)
    if total_count == 0:
        weighted_tone = sum(tones) / Decimal(len(tones))
    else:
        weighted_tone = sum(
            tone * count for tone, count in zip(tones, counts, strict=True)
        ) / Decimal(total_count)
    sentiment = (weighted_tone / Decimal("100")).quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)
    attention = (sum(coverages) / Decimal(len(coverages))).quantize(
        _QUANTUM,
        rounding=ROUND_HALF_EVEN,
    )
    conflict = any(tone < 0 for tone in tones) and any(tone > 0 for tone in tones)
    return _with_hash(
        {
            **base,
            "status": "AVAILABLE",
            "qualityStatus": "COMPLETE",
            "sentimentScore": float(sentiment),
            "attentionScore": float(attention),
            "articleCount": total_count,
            "conflictFlag": conflict,
            "summary": "합성 GDELT aggregate의 뉴스 톤과 관심도이며 설명 근거로만 사용한다.",
        }
    )


def _with_hash(value: dict[str, object]) -> dict[str, object]:
    digest = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return {**value, "artifactHash": digest}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise GdeltAggregateError("INVALID_RESPONSE", "summary time must be timezone aware")
    return value.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
