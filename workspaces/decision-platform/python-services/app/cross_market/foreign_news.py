"""Pre-S5 외신 sentiment의 sanitized, fixture-first materialization 경계다.

이 모듈은 기사 headline/summary/body, provider 응답, credential, query/header를 받거나 저장하지
않는다. provider transport는 별도 exact approval packet boundary가 열리기 전까지 이 모듈에
연결할 수 없고, 여기서는 이미 transient parse가 끝난 lane state와 허용된 digest/locator만
입력으로 받는다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final

from app.data._shared.canonical_json import canonical_json_bytes

FOREIGN_NEWS_LANES: Final[tuple[str, ...]] = (
    "FINNHUB_PERSONAL_LOCAL",
    "SEC_OFFICIAL",
    "FED_OFFICIAL",
    "GDELT_OFFLINE_REFERENCE",
)
MODEL_CANDIDATES: Final[tuple[str, ...]] = (
    "PROSUSAI_FINBERT",
    "YIYANGHKUST_FINBERT_TONE",
    "LOUGHRAN_MCDONALD_BASELINE",
)
_LANE_STATES: Final[frozenset[str]] = frozenset({"AVAILABLE", "ABSTAIN", "NOT_ACTIVATED"})
_TRANSIENT_LANE_STATES: Final[frozenset[str]] = frozenset({"AVAILABLE", "ABSTAIN"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^[0-9A-Z._:-]{1,20}$")
_OWNER = re.compile(r"^[A-Za-z0-9._:-]{3,128}$")
_SELECTION_ID = re.compile(r"^fns_[A-Za-z0-9_-]{12,96}$")
_FORBIDDEN_FIELD_FRAGMENT = re.compile(
    r"(?:article|attachment|body|content|credential|header|headline|query|raw|summary|title|url)",
    re.IGNORECASE,
)
_GDELT_V2_ALLOWED_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "abstainReason",
        "allowedUses",
        "articleCount",
        "articleMetadataStored",
        "artifactHash",
        "artifactId",
        "asOf",
        "attentionScore",
        "attribution",
        "availableAt",
        "conflictFlag",
        "decisionAuthority",
        "producer",
        "qualityStatus",
        "rawProviderDataStored",
        "riskDecisionHashIncluded",
        "s5FeatureEligible",
        "schemaVersion",
        "sentimentScore",
        "sourceObservationRefs",
        "sourceWorkspace",
        "status",
        "summary",
        "symbol",
    }
)
_GDELT_V2_REQUIRED_BASE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "allowedUses",
        "articleMetadataStored",
        "artifactHash",
        "artifactId",
        "asOf",
        "attribution",
        "availableAt",
        "decisionAuthority",
        "producer",
        "qualityStatus",
        "rawProviderDataStored",
        "riskDecisionHashIncluded",
        "s5FeatureEligible",
        "schemaVersion",
        "sourceObservationRefs",
        "sourceWorkspace",
        "status",
        "symbol",
    }
)
_GDELT_V2_AVAILABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {"articleCount", "attentionScore", "conflictFlag", "sentimentScore", "summary"}
)
_GDELT_V2_ABSTAIN_REASONS: Final[frozenset[str]] = frozenset(
    {
        "CONFLICT_UNRESOLVED",
        "INPUT_INCOMPLETE",
        "INPUT_STALE",
        "NO_OBSERVATIONS",
        "PRODUCER_FAILURE",
    }
)
_GDELT_V2_ATTRIBUTION: Final[dict[str, str]] = {
    "citation": "The GDELT Project",
    "projectUrl": "https://www.gdeltproject.org/",
    "provider": "GDELT",
    "termsUrl": "https://www.gdeltproject.org/about.html",
}
_GDELT_SYNTHETIC_SUMMARY: Final[str] = (
    "합성 GDELT aggregate의 뉴스 톤과 관심도이며 설명 근거로만 사용한다."
)
_GDELT_ARTIFACT_ID = re.compile(r"^news_sum_[a-z0-9][a-z0-9_-]+$")
_GDELT_OBSERVATION_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]+$")


class ForeignNewsSentimentError(ValueError):
    """외신 aggregate가 explanation-only, no-raw 경계를 벗어났음을 나타낸다."""


class ForeignNewsModelSelectionError(ValueError):
    """모델 validation/test-selection 순서가 계약을 위반했음을 나타낸다."""


@dataclass(frozen=True, slots=True)
class ForeignNewsLaneState:
    """API/DB에 남길 수 있는 lane의 최소 상태다. 숫자·본문·provider metadata는 보관하지 않는다."""

    lane_id: str
    state: str

    def __post_init__(self) -> None:
        if self.lane_id not in FOREIGN_NEWS_LANES:
            raise ForeignNewsSentimentError("FOREIGN_NEWS_LANE_INVALID")
        if self.state not in _LANE_STATES:
            raise ForeignNewsSentimentError("FOREIGN_NEWS_LANE_STATE_INVALID")

    def to_payload(self) -> dict[str, str]:
        return {"laneId": self.lane_id, "state": self.state}


@dataclass(frozen=True, slots=True)
class ForeignNewsTransientLaneAggregate:
    """bounded transient parser가 materializer에 넘기는 non-content lane proof다.

    ``content_hash``와 SEC/Fed provenance locator는 materialization 검증에만 쓰며, public
    response와 append-only payload에는 복사하지 않는다. Finnhub는 owner-local derived aggregate
    만 허용하므로 locator를 사용할 수 없다.
    """

    lane_id: str
    state: str
    content_hash: str | None
    official_release_locator: str | None

    def __post_init__(self) -> None:
        if self.lane_id not in FOREIGN_NEWS_LANES:
            raise ForeignNewsSentimentError("FOREIGN_NEWS_LANE_INVALID")
        if self.state not in _TRANSIENT_LANE_STATES:
            raise ForeignNewsSentimentError("FOREIGN_NEWS_LANE_STATE_INVALID")
        if self.content_hash is not None and _SHA256.fullmatch(self.content_hash) is None:
            raise ForeignNewsSentimentError("FOREIGN_NEWS_CONTENT_HASH_INVALID")
        if self.official_release_locator is not None and (
            not self.official_release_locator
            or len(self.official_release_locator) > 256
            or _FORBIDDEN_FIELD_FRAGMENT.search(self.official_release_locator)
        ):
            raise ForeignNewsSentimentError("FOREIGN_NEWS_LOCATOR_INVALID")
        if self.lane_id == "FINNHUB_PERSONAL_LOCAL" and self.official_release_locator is not None:
            raise ForeignNewsSentimentError("FOREIGN_NEWS_FINNHUB_LOCATOR_FORBIDDEN")
        if (
            self.lane_id in {"SEC_OFFICIAL", "FED_OFFICIAL"}
            and self.state == "AVAILABLE"
            and (self.content_hash is None or self.official_release_locator is None)
        ):
            raise ForeignNewsSentimentError("FOREIGN_NEWS_OFFICIAL_PROVENANCE_REQUIRED")


@dataclass(frozen=True, slots=True)
class ForeignNewsSentimentRecord:
    """authenticated owner에게만 읽히는 sanitized latest aggregate record다."""

    owner_user_id: str
    symbol: str
    as_of: datetime
    status: str
    lanes: tuple[ForeignNewsLaneState, ...]

    def __post_init__(self) -> None:
        _validate_owner(self.owner_user_id)
        _validate_symbol(self.symbol)
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ForeignNewsSentimentError("FOREIGN_NEWS_AS_OF_INVALID")
        if self.status not in {"AVAILABLE", "ABSTAIN"}:
            raise ForeignNewsSentimentError("FOREIGN_NEWS_STATUS_INVALID")
        if tuple(item.lane_id for item in self.lanes) != FOREIGN_NEWS_LANES:
            raise ForeignNewsSentimentError("FOREIGN_NEWS_LANE_ORDER_INVALID")
        available = any(item.state == "AVAILABLE" for item in self.lanes)
        if self.status == "AVAILABLE" and not available:
            raise ForeignNewsSentimentError("FOREIGN_NEWS_AVAILABLE_WITHOUT_LANE")
        if self.status == "ABSTAIN" and available:
            raise ForeignNewsSentimentError("FOREIGN_NEWS_ABSTAIN_WITH_AVAILABLE_LANE")

    def to_public_payload(self) -> dict[str, object]:
        """OpenAPI foreign-news response와 동일한 no-raw projection을 반환한다."""

        return {
            "allowedUses": ["EXPLANATION_ONLY"],
            "articleMetadataStored": False,
            "asOf": _instant(self.as_of),
            "contractId": "foreign-news-sentiment-v1",
            "decisionAuthority": "NONE",
            "lanes": [item.to_payload() for item in self.lanes],
            "rawProviderDataStored": False,
            "riskDecisionHashIncluded": False,
            "s5FeatureEligible": False,
            "schemaVersion": 1,
            "status": self.status,
            "symbol": self.symbol,
        }

    def to_storage_payload(self) -> dict[str, object]:
        """DB writer가 허용하는 payload는 API projection과 같아 raw retention drift를 막는다."""

        return self.to_public_payload()

    def to_writer_record(self) -> dict[str, object]:
        """append-only writer가 identity/replay를 확인할 hash-only wrapper를 만든다."""

        payload = self.to_storage_payload()
        payload_hash = _sha256(_canonical(payload))
        logical_identity_hash = _sha256(
            f"foreign-news-sentiment/v1|{self.owner_user_id}|{self.symbol}|{_instant(self.as_of)}".encode()
        )
        artifact_hash = _sha256(
            _canonical(
                {
                    "logicalIdentityHash": logical_identity_hash,
                    "payload": payload,
                    "payloadHash": payload_hash,
                }
            )
        )
        return {
            "artifactHash": artifact_hash,
            "logicalIdentityHash": logical_identity_hash,
            "payload": payload,
            "payloadHash": payload_hash,
        }

    @classmethod
    def from_storage_payload(
        cls,
        *,
        owner_user_id: str,
        payload: Mapping[str, object],
    ) -> ForeignNewsSentimentRecord:
        """DB reader가 malformed/raw payload를 API 직전 fail-closed하도록 재검증한다."""

        _reject_forbidden_payload(payload)
        expected_keys = {
            "allowedUses",
            "articleMetadataStored",
            "asOf",
            "contractId",
            "decisionAuthority",
            "lanes",
            "rawProviderDataStored",
            "riskDecisionHashIncluded",
            "s5FeatureEligible",
            "schemaVersion",
            "status",
            "symbol",
        }
        if set(payload) != expected_keys:
            raise ForeignNewsSentimentError("FOREIGN_NEWS_STORAGE_SHAPE_INVALID")
        if (
            payload.get("allowedUses") != ["EXPLANATION_ONLY"]
            or payload.get("articleMetadataStored") is not False
            or payload.get("contractId") != "foreign-news-sentiment-v1"
            or payload.get("decisionAuthority") != "NONE"
            or payload.get("rawProviderDataStored") is not False
            or payload.get("riskDecisionHashIncluded") is not False
            or payload.get("s5FeatureEligible") is not False
            or payload.get("schemaVersion") != 1
        ):
            raise ForeignNewsSentimentError("FOREIGN_NEWS_INVARIANT_INVALID")
        raw_lanes = payload.get("lanes")
        if not isinstance(raw_lanes, list):
            raise ForeignNewsSentimentError("FOREIGN_NEWS_STORAGE_SHAPE_INVALID")
        lanes = tuple(
            ForeignNewsLaneState(
                lane_id=_required_string(value, "laneId"),
                state=_required_string(value, "state"),
            )
            for value in raw_lanes
            if isinstance(value, Mapping)
        )
        if len(lanes) != len(raw_lanes):
            raise ForeignNewsSentimentError("FOREIGN_NEWS_STORAGE_SHAPE_INVALID")
        return cls(
            owner_user_id=owner_user_id,
            symbol=_required_string(payload, "symbol"),
            as_of=_parse_instant(_required_string(payload, "asOf")),
            status=_required_string(payload, "status"),
            lanes=lanes,
        )


@dataclass(frozen=True, slots=True)
class ForeignNewsSelectionMetrics:
    """validation dataset의 candidate-level aggregate metric만 보관한다. label/text는 포함하지 않는다."""

    candidate_model: str
    class_recalls: Mapping[str, float]
    cpu_p95_millis: float
    critical_negation_number_unit_errors: int
    ece: float
    footprint_bytes: int
    macro_f1: float
    neutral_f1: float

    def __post_init__(self) -> None:
        if self.candidate_model not in MODEL_CANDIDATES:
            raise ForeignNewsModelSelectionError("FOREIGN_NEWS_CANDIDATE_INVALID")
        if set(self.class_recalls) != {"NEGATIVE", "NEUTRAL", "POSITIVE"}:
            raise ForeignNewsModelSelectionError("FOREIGN_NEWS_CLASS_RECALL_INVALID")
        bounded = (
            *self.class_recalls.values(),
            self.ece,
            self.macro_f1,
            self.neutral_f1,
        )
        if any(isinstance(value, bool) or not 0 <= float(value) <= 1 for value in bounded):
            raise ForeignNewsModelSelectionError("FOREIGN_NEWS_METRIC_INVALID")
        if (
            isinstance(self.cpu_p95_millis, bool)
            or self.cpu_p95_millis < 0
            or self.critical_negation_number_unit_errors < 0
            or self.footprint_bytes < 0
        ):
            raise ForeignNewsModelSelectionError("FOREIGN_NEWS_METRIC_INVALID")

    def passes_validation(self) -> bool:
        return (
            self.macro_f1 >= 0.80
            and all(value >= 0.75 for value in self.class_recalls.values())
            and self.neutral_f1 >= 0.75
            and self.ece <= 0.10
            and self.critical_negation_number_unit_errors == 0
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "candidateModel": self.candidate_model,
            "metrics": {
                "classRecalls": {
                    "NEGATIVE": self.class_recalls["NEGATIVE"],
                    "NEUTRAL": self.class_recalls["NEUTRAL"],
                    "POSITIVE": self.class_recalls["POSITIVE"],
                },
                "cpuP95Millis": self.cpu_p95_millis,
                "criticalNegationNumberUnitErrors": self.critical_negation_number_unit_errors,
                "ece": self.ece,
                "footprintBytes": self.footprint_bytes,
                "macroF1": self.macro_f1,
                "neutralF1": self.neutral_f1,
            },
        }


@dataclass(frozen=True, slots=True)
class ForeignNewsSelectionRun:
    """exact three-model validation 후 selected model 하나만 test하는 immutable decision record다."""

    selection_id: str
    selection_generation: int
    validation_results: tuple[ForeignNewsSelectionMetrics, ...]
    selection_status: str
    selected_model: str | None
    test_evaluation_count: int
    test_outcome: str
    test_target_model: str | None
    abstain_reason: str | None

    @property
    def candidate_models(self) -> tuple[str, ...]:
        return tuple(item.candidate_model for item in self.validation_results)

    @classmethod
    def from_validation(
        cls,
        *,
        selection_id: str,
        selection_generation: int,
        results: Sequence[ForeignNewsSelectionMetrics],
    ) -> ForeignNewsSelectionRun:
        if _SELECTION_ID.fullmatch(selection_id) is None or selection_generation < 1:
            raise ForeignNewsModelSelectionError("FOREIGN_NEWS_SELECTION_ID_INVALID")
        ordered = tuple(results)
        if tuple(item.candidate_model for item in ordered) != MODEL_CANDIDATES:
            raise ForeignNewsModelSelectionError("FOREIGN_NEWS_CANDIDATE_ORDER_INVALID")
        eligible = tuple(item for item in ordered if item.passes_validation())
        if not eligible:
            return cls(
                selection_id=selection_id,
                selection_generation=selection_generation,
                validation_results=ordered,
                selection_status="ABSTAIN",
                selected_model=None,
                test_evaluation_count=0,
                test_outcome="NOT_RUN",
                test_target_model=None,
                abstain_reason="NO_MODEL_MEETS_VALIDATION_GATE",
            )
        ranked = tuple(sorted(eligible, key=_selection_ranking_key))
        if len(ranked) > 1 and _selection_ranking_key(ranked[0]) == _selection_ranking_key(
            ranked[1]
        ):
            return cls(
                selection_id=selection_id,
                selection_generation=selection_generation,
                validation_results=ordered,
                selection_status="ABSTAIN",
                selected_model=None,
                test_evaluation_count=0,
                test_outcome="NOT_RUN",
                test_target_model=None,
                abstain_reason="TIE_AFTER_FOOTPRINT",
            )
        return cls(
            selection_id=selection_id,
            selection_generation=selection_generation,
            validation_results=ordered,
            selection_status="SELECTED_PENDING_TEST",
            selected_model=ranked[0].candidate_model,
            test_evaluation_count=0,
            test_outcome="NOT_RUN",
            test_target_model=None,
            abstain_reason=None,
        )

    def record_selected_model_test(self, *, passed: bool) -> ForeignNewsSelectionRun:
        """validation winner만 단 한 번 test set으로 평가하고 차순위 재시도를 금지한다."""

        if self.test_evaluation_count != 0:
            raise ForeignNewsModelSelectionError("FOREIGN_NEWS_TEST_ALREADY_EVALUATED")
        if self.selection_status != "SELECTED_PENDING_TEST" or self.selected_model is None:
            raise ForeignNewsModelSelectionError("FOREIGN_NEWS_TEST_NOT_ELIGIBLE")
        if passed:
            return replace(
                self,
                selection_status="TEST_EVALUATED",
                test_evaluation_count=1,
                test_outcome="PASSED",
                test_target_model=self.selected_model,
            )
        return replace(
            self,
            selection_status="ABSTAIN",
            test_evaluation_count=1,
            test_outcome="FAILED",
            test_target_model=self.selected_model,
            abstain_reason="TEST_FAILED",
        )

    def to_storage_payload(self) -> dict[str, object]:
        """foreign-news-model-selection-v1 schema와 같은 metric-only persistence payload다."""

        return {
            "abstainReason": self.abstain_reason,
            "candidateModels": list(MODEL_CANDIDATES),
            "schemaVersion": 1,
            "selectionGeneration": self.selection_generation,
            "selectionId": self.selection_id,
            "selectedModel": self.selected_model,
            "selectionStatus": self.selection_status,
            "testEvaluationCount": self.test_evaluation_count,
            "testOutcome": self.test_outcome,
            "testTargetModel": self.test_target_model,
            "validationCompleted": True,
            "validationResults": [item.to_payload() for item in self.validation_results],
        }


class ForeignNewsSentimentMaterializer:
    """fixture/approved transient lane aggregate를 response-safe immutable record로 축소한다."""

    def materialize(
        self,
        *,
        owner_user_id: str,
        symbol: str,
        as_of: datetime,
        aggregates: Sequence[ForeignNewsTransientLaneAggregate],
    ) -> ForeignNewsSentimentRecord:
        _validate_owner(owner_user_id)
        _validate_symbol(symbol)
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ForeignNewsSentimentError("FOREIGN_NEWS_AS_OF_INVALID")
        states = dict.fromkeys(FOREIGN_NEWS_LANES, "NOT_ACTIVATED")
        for aggregate in aggregates:
            if aggregate.lane_id in states and states[aggregate.lane_id] != "NOT_ACTIVATED":
                raise ForeignNewsSentimentError("FOREIGN_NEWS_LANE_DUPLICATE")
            states[aggregate.lane_id] = aggregate.state
        lanes = tuple(
            ForeignNewsLaneState(lane_id=lane, state=states[lane]) for lane in FOREIGN_NEWS_LANES
        )
        return ForeignNewsSentimentRecord(
            owner_user_id=owner_user_id,
            symbol=symbol,
            as_of=as_of.astimezone(UTC),
            status="AVAILABLE" if any(item.state == "AVAILABLE" for item in lanes) else "ABSTAIN",
            lanes=lanes,
        )

    def from_gdelt_offline_reference(
        self,
        *,
        owner_user_id: str,
        symbol: str,
        as_of: datetime,
        gdelt_summary: Mapping[str, object],
    ) -> ForeignNewsSentimentRecord:
        """기존 Decision-owned GDELT offline summary를 HTTP adapter 없이 state 하나로만 재사용한다."""

        _validate_gdelt_summary(
            gdelt_summary,
            expected_as_of=as_of,
            expected_symbol=symbol,
        )
        state = "AVAILABLE" if gdelt_summary.get("status") == "AVAILABLE" else "ABSTAIN"
        artifact_hash = gdelt_summary.get("artifactHash")
        return self.materialize(
            owner_user_id=owner_user_id,
            symbol=symbol,
            as_of=as_of,
            aggregates=(
                ForeignNewsTransientLaneAggregate(
                    lane_id="GDELT_OFFLINE_REFERENCE",
                    state=state,
                    content_hash=artifact_hash if isinstance(artifact_hash, str) else None,
                    official_release_locator=None,
                ),
            ),
        )


def _validate_gdelt_summary(
    summary: Mapping[str, object],
    *,
    expected_as_of: datetime,
    expected_symbol: str,
) -> None:
    """기존 v2 aggregate의 allowlisted metadata만 검증하고 원문 성격 필드는 절대 복사하지 않는다."""

    keys = set(summary)
    if keys - _GDELT_V2_ALLOWED_FIELDS or not keys >= _GDELT_V2_REQUIRED_BASE_FIELDS:
        raise ForeignNewsSentimentError("FOREIGN_NEWS_GDELT_BOUNDARY_INVALID")
    required = {
        "allowedUses": ["EXPLANATION_ONLY"],
        "articleMetadataStored": False,
        "decisionAuthority": "NONE",
        "producer": "NEWS_SENTIMENT_AGGREGATOR",
        "rawProviderDataStored": False,
        "riskDecisionHashIncluded": False,
        "schemaVersion": "2",
        "s5FeatureEligible": False,
        "sourceWorkspace": "decision-platform",
    }
    if any(summary.get(key) != value for key, value in required.items()):
        raise ForeignNewsSentimentError("FOREIGN_NEWS_GDELT_BOUNDARY_INVALID")
    artifact_id = summary.get("artifactId")
    artifact_hash = summary.get("artifactHash")
    if (
        not isinstance(artifact_id, str)
        or _GDELT_ARTIFACT_ID.fullmatch(artifact_id) is None
        or not isinstance(artifact_hash, str)
        or _SHA256.fullmatch(artifact_hash) is None
        or summary.get("symbol") != expected_symbol
    ):
        raise ForeignNewsSentimentError("FOREIGN_NEWS_GDELT_BOUNDARY_INVALID")
    try:
        as_of = _parse_instant(_required_string(summary, "asOf"))
        available_at = _parse_instant(_required_string(summary, "availableAt"))
    except ForeignNewsSentimentError as error:
        raise ForeignNewsSentimentError("FOREIGN_NEWS_GDELT_BOUNDARY_INVALID") from error
    if (
        expected_as_of.tzinfo is None
        or expected_as_of.utcoffset() is None
        or as_of != expected_as_of.astimezone(UTC)
        or as_of > available_at
    ):
        raise ForeignNewsSentimentError("FOREIGN_NEWS_GDELT_BOUNDARY_INVALID")
    if summary.get("attribution") != _GDELT_V2_ATTRIBUTION:
        raise ForeignNewsSentimentError("FOREIGN_NEWS_GDELT_BOUNDARY_INVALID")
    _validate_gdelt_source_observation_refs(summary.get("sourceObservationRefs"))
    _validate_gdelt_payload_by_status(summary)

    expected_hash_payload = dict(summary)
    expected_hash_payload.pop("artifactHash")
    if _sha256(canonical_json_bytes(expected_hash_payload)) != artifact_hash:
        raise ForeignNewsSentimentError("FOREIGN_NEWS_GDELT_BOUNDARY_INVALID")


def _validate_gdelt_source_observation_refs(value: object) -> None:
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise ForeignNewsSentimentError("FOREIGN_NEWS_GDELT_BOUNDARY_INVALID")
    for reference in value:
        if not isinstance(reference, Mapping) or set(reference) != {
            "artifactHash",
            "observationId",
        }:
            raise ForeignNewsSentimentError("FOREIGN_NEWS_GDELT_BOUNDARY_INVALID")
        artifact_hash = reference.get("artifactHash")
        observation_id = reference.get("observationId")
        if (
            not isinstance(artifact_hash, str)
            or _SHA256.fullmatch(artifact_hash) is None
            or not isinstance(observation_id, str)
            or _GDELT_OBSERVATION_ID.fullmatch(observation_id) is None
        ):
            raise ForeignNewsSentimentError("FOREIGN_NEWS_GDELT_BOUNDARY_INVALID")


def _validate_gdelt_payload_by_status(summary: Mapping[str, object]) -> None:
    status = summary.get("status")
    if status == "AVAILABLE":
        if not set(summary) >= _GDELT_V2_AVAILABLE_FIELDS:
            raise ForeignNewsSentimentError("FOREIGN_NEWS_GDELT_BOUNDARY_INVALID")
        article_count = summary.get("articleCount")
        if (
            isinstance(article_count, bool)
            or not isinstance(article_count, int)
            or not 1 <= article_count <= 1_000_000_000
            or summary.get("qualityStatus") != "COMPLETE"
            or summary.get("summary") != _GDELT_SYNTHETIC_SUMMARY
        ):
            raise ForeignNewsSentimentError("FOREIGN_NEWS_GDELT_BOUNDARY_INVALID")
        for field in ("attentionScore", "sentimentScore"):
            value = summary.get(field)
            if isinstance(value, bool) or not isinstance(value, (float, int)):
                raise ForeignNewsSentimentError("FOREIGN_NEWS_GDELT_BOUNDARY_INVALID")
        if not isinstance(summary.get("conflictFlag"), bool) or "abstainReason" in summary:
            raise ForeignNewsSentimentError("FOREIGN_NEWS_GDELT_BOUNDARY_INVALID")
        return
    if status == "ABSTAIN":
        if (
            any(field in summary for field in _GDELT_V2_AVAILABLE_FIELDS)
            or summary.get("abstainReason") not in _GDELT_V2_ABSTAIN_REASONS
        ):
            raise ForeignNewsSentimentError("FOREIGN_NEWS_GDELT_BOUNDARY_INVALID")
        return
    raise ForeignNewsSentimentError("FOREIGN_NEWS_GDELT_BOUNDARY_INVALID")


def _selection_ranking_key(item: ForeignNewsSelectionMetrics) -> tuple[float, float, float, int]:
    return (-item.macro_f1, item.ece, item.cpu_p95_millis, item.footprint_bytes)


def _reject_forbidden_payload(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).replace("_", "").casefold()
            if normalized in {"articlemetadatastored", "rawproviderdatastored"}:
                if child is not False:
                    raise ForeignNewsSentimentError("FOREIGN_NEWS_RAW_FIELD_FORBIDDEN")
            elif _FORBIDDEN_FIELD_FRAGMENT.search(normalized):
                raise ForeignNewsSentimentError("FOREIGN_NEWS_RAW_FIELD_FORBIDDEN")
            _reject_forbidden_payload(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _reject_forbidden_payload(child)


def _required_string(value: Mapping[str, object], field: str) -> str:
    candidate = value.get(field)
    if not isinstance(candidate, str):
        raise ForeignNewsSentimentError("FOREIGN_NEWS_STORAGE_SHAPE_INVALID")
    return candidate


def _validate_owner(value: str) -> None:
    if _OWNER.fullmatch(value) is None:
        raise ForeignNewsSentimentError("FOREIGN_NEWS_OWNER_INVALID")


def _validate_symbol(value: str) -> None:
    if _SYMBOL.fullmatch(value) is None:
        raise ForeignNewsSentimentError("FOREIGN_NEWS_SYMBOL_INVALID")


def _parse_instant(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ForeignNewsSentimentError("FOREIGN_NEWS_AS_OF_INVALID") from error
    if parsed.tzinfo is None:
        raise ForeignNewsSentimentError("FOREIGN_NEWS_AS_OF_INVALID")
    return parsed.astimezone(UTC)


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
