from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final, Mapping, Sequence, cast


class ExplanationProjectionError(ValueError):
    """S4.8C evidence가 설명 전용 계약이나 시점 경계를 벗어났음을 나타낸다."""


_CLASSIFICATIONS: Final[frozenset[str]] = frozenset(
    {"CONFIRMED_FACT", "REPORTED_CLAIM", "MARKET_INTERPRETATION", "HYPOTHESIS"}
)
_RELATIONS: Final[frozenset[str]] = frozenset(
    {"PRECEDES", "CO_MOVES_WITH", "REPORTED_AS_CAUSE", "CORROBORATES", "CONTRADICTS"}
)
_SHA256_CHARS: Final[frozenset[str]] = frozenset("0123456789abcdef")


@dataclass(frozen=True, slots=True)
class CauseExplanation:
    logical_identity_hash: str
    classification: str
    relation: str
    counterargument: bool
    retracted: bool
    supersedes_evidence_id: str | None
    contradiction_evidence_ids: tuple[str, ...]
    sanitized_summary: str
    causal_claim: bool = False

    def to_canonical(self) -> dict[str, object]:
        return {
            "causalClaim": self.causal_claim,
            "classification": self.classification,
            "contradictionEvidenceIds": list(self.contradiction_evidence_ids),
            "counterargument": self.counterargument,
            "logicalIdentityHash": self.logical_identity_hash,
            "relation": self.relation,
            "retracted": self.retracted,
            "sanitizedSummary": self.sanitized_summary,
            "supersedesEvidenceId": self.supersedes_evidence_id,
        }


@dataclass(frozen=True, slots=True)
class AnalystRevisionProjection:
    logical_identity_hash: str
    broker_id: str
    rating: str
    target_price_delta: Decimal
    eps_delta: Decimal
    revenue_delta: Decimal
    retracted: bool

    def to_canonical(self) -> dict[str, object]:
        return {
            "brokerId": self.broker_id,
            "epsDelta": _decimal(self.eps_delta),
            "logicalIdentityHash": self.logical_identity_hash,
            "rating": self.rating,
            "retracted": self.retracted,
            "revenueDelta": _decimal(self.revenue_delta),
            "targetPriceDelta": _decimal(self.target_price_delta),
        }


@dataclass(frozen=True, slots=True)
class AnalystExplanation:
    status: str
    distinct_broker_count: int
    directional_weight: Decimal | None
    revisions: tuple[AnalystRevisionProjection, ...]

    def to_canonical(self) -> dict[str, object]:
        return {
            "directionalWeight": (
                None if self.directional_weight is None else _decimal(self.directional_weight)
            ),
            "distinctBrokerCount": self.distinct_broker_count,
            "revisions": [revision.to_canonical() for revision in self.revisions],
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class CrossMarketExplanation:
    symbol: str
    evaluated_at: datetime
    causes: tuple[CauseExplanation, ...]
    analyst: AnalystExplanation
    gdelt_aggregate_status: str
    decision_authority: str = "NONE"
    risk_decision_hash_included: bool = False
    s5_feature_eligible: bool = False
    rag_corpus_eligible: bool = False
    provider_physical_calls: int = 0
    external_llm_calls: int = 0

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            {
                "analyst": self.analyst.to_canonical(),
                "causes": [cause.to_canonical() for cause in self.causes],
                "decisionAuthority": self.decision_authority,
                "evaluatedAt": _instant(self.evaluated_at),
                "externalLlmCalls": self.external_llm_calls,
                "gdeltAggregateStatus": self.gdelt_aggregate_status,
                "providerPhysicalCalls": self.provider_physical_calls,
                "ragCorpusEligible": self.rag_corpus_eligible,
                "riskDecisionHashIncluded": self.risk_decision_hash_included,
                "s5FeatureEligible": self.s5_feature_eligible,
                "symbol": self.symbol,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


class CrossMarketExplanationProjector:
    """sanitized analyst/cause/GDELT aggregate를 authority 없는 설명으로만 투영한다."""

    def project(
        self,
        *,
        symbol: str,
        analyst_evidence: Sequence[Mapping[str, object]],
        cause_evidence: Sequence[Mapping[str, object]],
        gdelt_summary: Mapping[str, object],
        evaluated_at: datetime,
    ) -> CrossMarketExplanation:
        if not symbol or len(symbol) > 32 or evaluated_at.tzinfo is None:
            raise ExplanationProjectionError("EXPLANATION_INPUT_INVALID")
        _validate_gdelt_summary(gdelt_summary)
        causes = tuple(
            sorted(
                (_cause(item, evaluated_at) for item in cause_evidence),
                key=lambda item: item.logical_identity_hash.encode("utf-8"),
            )
        )
        revisions = tuple(
            sorted(
                (
                    _analyst(item, evaluated_at)
                    for item in analyst_evidence
                    if item.get("symbol") == symbol
                ),
                key=lambda item: item.logical_identity_hash.encode("utf-8"),
            )
        )
        active_brokers = {item.broker_id for item in revisions if not item.retracted}
        coverage_available = len(active_brokers) >= 3
        analyst = AnalystExplanation(
            status="AVAILABLE" if coverage_available else "INSUFFICIENT_COVERAGE",
            distinct_broker_count=len(active_brokers),
            # BUY/매수 level 자체는 방향성 evidence가 아니므로 exact zero만 허용한다.
            directional_weight=Decimal(0) if coverage_available else None,
            revisions=revisions,
        )
        return CrossMarketExplanation(
            symbol=symbol,
            evaluated_at=evaluated_at.astimezone(UTC),
            causes=causes,
            analyst=analyst,
            gdelt_aggregate_status=str(gdelt_summary["status"]),
        )


def _cause(value: Mapping[str, object], evaluated_at: datetime) -> CauseExplanation:
    identity = _hash(value.get("logicalIdentityHash"))
    classification = str(value.get("classification"))
    relation = str(value.get("relation"))
    if classification not in _CLASSIFICATIONS or relation not in _RELATIONS:
        raise ExplanationProjectionError("CAUSE_ENUM_INVALID")
    if str(value.get("decisionAuthority")) != "NONE":
        raise ExplanationProjectionError("CAUSE_AUTHORITY_FORBIDDEN")
    if str(value.get("sourceFamily")) == "GDELT_AGGREGATE" and (
        classification == "CONFIRMED_FACT" or relation == "REPORTED_AS_CAUSE"
    ):
        raise ExplanationProjectionError("GDELT_CAUSALITY_FORBIDDEN")
    if _parse_instant(value.get("availableAt")) > evaluated_at.astimezone(UTC):
        raise ExplanationProjectionError("FUTURE_EVIDENCE")
    contradictions = _string_tuple(value.get("contradictionEvidenceIds"), maximum=32)
    summary = str(value.get("sanitizedSummary", ""))
    if not 1 <= len(summary) <= 1000:
        raise ExplanationProjectionError("CAUSE_SUMMARY_INVALID")
    supersedes = value.get("supersedesEvidenceId")
    if supersedes is not None and (not isinstance(supersedes, str) or len(supersedes) > 128):
        raise ExplanationProjectionError("CAUSE_SUPERSEDE_INVALID")
    return CauseExplanation(
        logical_identity_hash=identity,
        classification=classification,
        relation=relation,
        counterargument=value.get("counterargument") is True,
        retracted=value.get("retracted") is True,
        supersedes_evidence_id=supersedes,
        contradiction_evidence_ids=tuple(sorted(contradictions, key=lambda item: item.encode("utf-8"))),
        sanitized_summary=summary,
    )


def _analyst(value: Mapping[str, object], evaluated_at: datetime) -> AnalystRevisionProjection:
    identity = _hash(value.get("logicalIdentityHash"))
    if (
        str(value.get("decisionAuthority")) != "NONE"
        or value.get("rawTextStored") is not False
        or _number(value.get("buyOpinionWeight")) != 0
        or _parse_instant(value.get("availableAt")) > evaluated_at.astimezone(UTC)
    ):
        raise ExplanationProjectionError("ANALYST_AUTHORITY_INVALID")
    broker_id = str(value.get("brokerId", ""))
    current = value.get("current")
    revision = value.get("revision")
    if not broker_id.startswith("broker_") or not isinstance(current, Mapping) or not isinstance(revision, Mapping):
        raise ExplanationProjectionError("ANALYST_PROJECTION_INVALID")
    rating = str(current.get("rating", ""))
    if not rating or len(rating) > 32:
        raise ExplanationProjectionError("ANALYST_PROJECTION_INVALID")
    return AnalystRevisionProjection(
        logical_identity_hash=identity,
        broker_id=broker_id,
        rating=rating,
        target_price_delta=_number(revision.get("targetPriceDelta")),
        eps_delta=_number(revision.get("epsDelta")),
        revenue_delta=_number(revision.get("revenueDelta")),
        retracted=value.get("retracted") is True,
    )


def _validate_gdelt_summary(value: Mapping[str, object]) -> None:
    if (
        value.get("allowedUses") != ["EXPLANATION_ONLY"]
        or value.get("decisionAuthority") != "NONE"
        or value.get("riskDecisionHashIncluded") is not False
        or value.get("s5FeatureEligible") is not False
        or value.get("rawProviderDataStored") is not False
        or value.get("articleMetadataStored") is not False
        or value.get("status") not in {"AVAILABLE", "ABSTAIN"}
    ):
        raise ExplanationProjectionError("GDELT_EXPLANATION_BOUNDARY_INVALID")


def _hash(value: object) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in _SHA256_CHARS for character in value):
        raise ExplanationProjectionError("EVIDENCE_HASH_INVALID")
    return value


def _parse_instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise ExplanationProjectionError("EVIDENCE_TIME_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ExplanationProjectionError("EVIDENCE_TIME_INVALID") from error
    if parsed.tzinfo is None:
        raise ExplanationProjectionError("EVIDENCE_TIME_INVALID")
    return parsed.astimezone(UTC)


def _number(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ExplanationProjectionError("EVIDENCE_NUMBER_INVALID")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ExplanationProjectionError("EVIDENCE_NUMBER_INVALID")
    return number


def _string_tuple(value: object, *, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ExplanationProjectionError("EVIDENCE_RELATION_INVALID")
    if any(not isinstance(item, str) or not item or len(item) > 128 for item in value):
        raise ExplanationProjectionError("EVIDENCE_RELATION_INVALID")
    return tuple(cast(list[str], value))


def _decimal(value: Decimal) -> str:
    return format(value, "f")


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
