"""외신 sentiment 후보를 local-only로 평가하고 선택 순서를 고정하는 harness다.

이 모듈은 이미 local owner가 적법하게 준비한 model runner와 비공개 evaluation input을 메모리에서만
소비한다. headline·summary·body·label·prediction은 반환값, DB, log, receipt에 넣지 않으며 provider
transport나 model download를 만들지 않는다.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from app.cross_market.foreign_news import (
    MODEL_CANDIDATES,
    ForeignNewsModelSelectionError,
    ForeignNewsSelectionMetrics,
    ForeignNewsSelectionRun,
)


_LABELS: Final[tuple[str, ...]] = ("NEGATIVE", "NEUTRAL", "POSITIVE")
_MAX_EVALUATION_EXAMPLES: Final[int] = 10_000
_MAX_TRANSIENT_TEXT_BYTES: Final[int] = 32 * 1024
_ECE_BIN_COUNT: Final[int] = 10


@dataclass(frozen=True, slots=True)
class ForeignNewsEvaluationExample:
    """평가 중에만 존재하는 one-label input이다. text는 저장이나 receipt로 투영되지 않는다."""

    text: str
    expected_label: str
    critical_negation_number_unit: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.text, str)
            or not self.text
            or "\x00" in self.text
            or len(self.text.encode("utf-8")) > _MAX_TRANSIENT_TEXT_BYTES
        ):
            raise ForeignNewsModelSelectionError("FOREIGN_NEWS_EVALUATION_TEXT_INVALID")
        if self.expected_label not in _LABELS:
            raise ForeignNewsModelSelectionError("FOREIGN_NEWS_EVALUATION_LABEL_INVALID")
        if type(self.critical_negation_number_unit) is not bool:
            raise ForeignNewsModelSelectionError("FOREIGN_NEWS_EVALUATION_CRITICAL_FLAG_INVALID")


@dataclass(frozen=True, slots=True)
class ForeignNewsPrediction:
    """local runner가 반환하는 label/confidence pair다. source content는 재노출하지 않는다."""

    label: str
    confidence: float

    def __post_init__(self) -> None:
        if self.label not in _LABELS:
            raise ForeignNewsModelSelectionError("FOREIGN_NEWS_PREDICTION_LABEL_INVALID")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (float, int))
            or not math.isfinite(float(self.confidence))
            or not 0.0 <= float(self.confidence) <= 1.0
        ):
            raise ForeignNewsModelSelectionError("FOREIGN_NEWS_PREDICTION_CONFIDENCE_INVALID")


class ForeignNewsLocalClassifier(Protocol):
    """runner는 in-memory text를 label로만 변환하고 provider/model artifact I/O를 소유하지 않는다."""

    def predict(self, text: str) -> ForeignNewsPrediction: ...


@dataclass(frozen=True, slots=True)
class ForeignNewsLocalCandidate:
    """fixed candidate ID와 local runner/footprint를 pair로 고정한다."""

    candidate_model: str
    classifier: ForeignNewsLocalClassifier
    footprint_bytes: int

    def __post_init__(self) -> None:
        if self.candidate_model not in MODEL_CANDIDATES:
            raise ForeignNewsModelSelectionError("FOREIGN_NEWS_CANDIDATE_INVALID")
        if not callable(getattr(self.classifier, "predict", None)):
            raise ForeignNewsModelSelectionError("FOREIGN_NEWS_CLASSIFIER_INVALID")
        if (
            isinstance(self.footprint_bytes, bool)
            or not isinstance(self.footprint_bytes, int)
            or self.footprint_bytes < 0
        ):
            raise ForeignNewsModelSelectionError("FOREIGN_NEWS_FOOTPRINT_INVALID")


@dataclass(frozen=True, slots=True)
class ForeignNewsSelectedTestResult:
    """selected-model test 후 metrics-only result와 immutable selection transition을 반환한다."""

    selection: ForeignNewsSelectionRun
    metrics: ForeignNewsSelectionMetrics


@dataclass(frozen=True, slots=True)
class ForeignNewsEvaluationHarness:
    """validation exact-three → selected one-time test 순서를 local measurement로 강제한다.

    caller는 persisted selection의 최신 state를 다시 읽어 전달해야 한다. 이 class는 raw evaluation
    inputs를 cache하거나 export하지 않으므로 DB의 one-time selection writer가 concurrent replay도
    별도로 차단해야 한다.
    """

    clock_ns: Callable[[], int] = time.perf_counter_ns

    def __post_init__(self) -> None:
        if not callable(self.clock_ns):
            raise ForeignNewsModelSelectionError("FOREIGN_NEWS_EVALUATION_CLOCK_INVALID")

    def evaluate_validation(
        self,
        *,
        selection_id: str,
        selection_generation: int,
        candidates: Sequence[ForeignNewsLocalCandidate],
        examples: Sequence[ForeignNewsEvaluationExample],
    ) -> ForeignNewsSelectionRun:
        """정확히 세 후보를 같은 transient validation set으로 평가한 뒤 선택만 반환한다."""

        ordered = _validate_exact_candidates(candidates)
        metrics = tuple(self.evaluate_candidate(candidate=item, examples=examples) for item in ordered)
        return ForeignNewsSelectionRun.from_validation(
            selection_id=selection_id,
            selection_generation=selection_generation,
            results=metrics,
        )

    def evaluate_selected_test(
        self,
        *,
        selection: ForeignNewsSelectionRun,
        candidates: Sequence[ForeignNewsLocalCandidate],
        examples: Sequence[ForeignNewsEvaluationExample],
    ) -> ForeignNewsSelectedTestResult:
        """선택된 runner 하나만 test하고 실패 때 차순위 model로 바꾸지 않는다."""

        if selection.test_evaluation_count != 0:
            raise ForeignNewsModelSelectionError("FOREIGN_NEWS_TEST_ALREADY_EVALUATED")
        if selection.selection_status != "SELECTED_PENDING_TEST" or selection.selected_model is None:
            raise ForeignNewsModelSelectionError("FOREIGN_NEWS_TEST_NOT_ELIGIBLE")
        ordered = _validate_exact_candidates(candidates)
        selected = next(
            (item for item in ordered if item.candidate_model == selection.selected_model),
            None,
        )
        if selected is None:  # Defensive narrowing after the exact candidate validation.
            raise ForeignNewsModelSelectionError("FOREIGN_NEWS_TEST_NOT_ELIGIBLE")
        metrics = self.evaluate_candidate(candidate=selected, examples=examples)
        return ForeignNewsSelectedTestResult(
            selection=selection.record_selected_model_test(passed=metrics.passes_validation()),
            metrics=metrics,
        )

    def evaluate_candidate(
        self,
        *,
        candidate: ForeignNewsLocalCandidate,
        examples: Sequence[ForeignNewsEvaluationExample],
    ) -> ForeignNewsSelectionMetrics:
        """one local candidate를 측정해 metric-only ``ForeignNewsSelectionMetrics``로 축소한다."""

        checked_examples = _validate_examples(examples)
        confusion = {
            expected: {predicted: 0 for predicted in _LABELS}
            for expected in _LABELS
        }
        bin_counts = [0] * _ECE_BIN_COUNT
        bin_confidence_sums = [0.0] * _ECE_BIN_COUNT
        bin_correct_counts = [0] * _ECE_BIN_COUNT
        elapsed_millis: list[float] = []
        critical_errors = 0

        for example in checked_examples:
            started = _clock_value(self.clock_ns)
            try:
                prediction = candidate.classifier.predict(example.text)
            except Exception as error:
                # local model failure도 raw input 없이 typed evaluation failure로만 남긴다.
                raise ForeignNewsModelSelectionError("FOREIGN_NEWS_MODEL_EXECUTION_FAILED") from error
            finished = _clock_value(self.clock_ns)
            if not isinstance(prediction, ForeignNewsPrediction):
                raise ForeignNewsModelSelectionError("FOREIGN_NEWS_PREDICTION_INVALID")
            elapsed_millis.append((finished - started) / 1_000_000.0)
            confusion[example.expected_label][prediction.label] += 1
            correct = prediction.label == example.expected_label
            if example.critical_negation_number_unit and not correct:
                critical_errors += 1
            bin_index = min(int(float(prediction.confidence) * _ECE_BIN_COUNT), _ECE_BIN_COUNT - 1)
            bin_counts[bin_index] += 1
            bin_confidence_sums[bin_index] += float(prediction.confidence)
            bin_correct_counts[bin_index] += int(correct)

        recalls = _class_recalls(confusion)
        f1_by_label = _class_f1_scores(confusion)
        return ForeignNewsSelectionMetrics(
            candidate_model=candidate.candidate_model,
            class_recalls=recalls,
            cpu_p95_millis=_p95(elapsed_millis),
            critical_negation_number_unit_errors=critical_errors,
            ece=_expected_calibration_error(
                total=len(checked_examples),
                bin_counts=bin_counts,
                bin_confidence_sums=bin_confidence_sums,
                bin_correct_counts=bin_correct_counts,
            ),
            footprint_bytes=candidate.footprint_bytes,
            macro_f1=sum(f1_by_label.values()) / len(_LABELS),
            neutral_f1=f1_by_label["NEUTRAL"],
        )


def _validate_exact_candidates(
    candidates: Sequence[ForeignNewsLocalCandidate],
) -> tuple[ForeignNewsLocalCandidate, ...]:
    ordered = tuple(candidates)
    if tuple(item.candidate_model for item in ordered) != MODEL_CANDIDATES:
        raise ForeignNewsModelSelectionError("FOREIGN_NEWS_CANDIDATE_ORDER_INVALID")
    return ordered


def _validate_examples(
    examples: Sequence[ForeignNewsEvaluationExample],
) -> tuple[ForeignNewsEvaluationExample, ...]:
    checked = tuple(examples)
    if not 3 <= len(checked) <= _MAX_EVALUATION_EXAMPLES:
        raise ForeignNewsModelSelectionError("FOREIGN_NEWS_EVALUATION_COUNT_INVALID")
    if any(not isinstance(item, ForeignNewsEvaluationExample) for item in checked):
        raise ForeignNewsModelSelectionError("FOREIGN_NEWS_EVALUATION_EXAMPLE_INVALID")
    if {item.expected_label for item in checked} != set(_LABELS):
        raise ForeignNewsModelSelectionError("FOREIGN_NEWS_EVALUATION_CLASS_COVERAGE")
    return checked


def _clock_value(clock_ns: Callable[[], int]) -> int:
    value = clock_ns()
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ForeignNewsModelSelectionError("FOREIGN_NEWS_EVALUATION_CLOCK_INVALID")
    return value


def _class_recalls(confusion: Mapping[str, Mapping[str, int]]) -> dict[str, float]:
    return {
        label: confusion[label][label] / sum(confusion[label].values())
        for label in _LABELS
    }


def _class_f1_scores(confusion: Mapping[str, Mapping[str, int]]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for label in _LABELS:
        true_positive = confusion[label][label]
        false_negative = sum(confusion[label].values()) - true_positive
        false_positive = sum(confusion[expected][label] for expected in _LABELS) - true_positive
        denominator = 2 * true_positive + false_positive + false_negative
        scores[label] = 0.0 if denominator == 0 else (2 * true_positive) / denominator
    return scores


def _expected_calibration_error(
    *,
    total: int,
    bin_counts: Sequence[int],
    bin_confidence_sums: Sequence[float],
    bin_correct_counts: Sequence[int],
) -> float:
    if total < 1:
        raise ForeignNewsModelSelectionError("FOREIGN_NEWS_EVALUATION_COUNT_INVALID")
    error = 0.0
    for count, confidence_sum, correct_count in zip(
        bin_counts,
        bin_confidence_sums,
        bin_correct_counts,
        strict=True,
    ):
        if count:
            accuracy = correct_count / count
            confidence = confidence_sum / count
            error += (count / total) * abs(accuracy - confidence)
    return error


def _p95(values: Sequence[float]) -> float:
    if not values:
        raise ForeignNewsModelSelectionError("FOREIGN_NEWS_EVALUATION_COUNT_INVALID")
    ordered = sorted(values)
    index = math.ceil(len(ordered) * 0.95) - 1
    return ordered[index]
