from __future__ import annotations

from collections.abc import Callable

import pytest

from app.cross_market.foreign_news import MODEL_CANDIDATES, ForeignNewsModelSelectionError
from app.cross_market.foreign_news_evaluator import (
    ForeignNewsEvaluationExample,
    ForeignNewsEvaluationHarness,
    ForeignNewsLocalCandidate,
    ForeignNewsPrediction,
)


def test_validation_measures_all_exact_candidates_and_runs_only_the_selected_model_on_test() -> (
    None
):
    prosus = _Classifier(
        {
            "validation-negative": ForeignNewsPrediction("NEGATIVE", 1.0),
            "validation-neutral": ForeignNewsPrediction("NEUTRAL", 1.0),
            "validation-positive": ForeignNewsPrediction("POSITIVE", 1.0),
            "test-critical-negative": ForeignNewsPrediction("POSITIVE", 1.0),
            "test-neutral": ForeignNewsPrediction("NEUTRAL", 1.0),
            "test-positive": ForeignNewsPrediction("POSITIVE", 1.0),
        }
    )
    tone = _Classifier(
        {
            "validation-negative": ForeignNewsPrediction("NEGATIVE", 1.0),
            "validation-neutral": ForeignNewsPrediction("NEUTRAL", 1.0),
            "validation-positive": ForeignNewsPrediction("POSITIVE", 1.0),
        },
        fail_on_test=True,
    )
    baseline = _Classifier(
        {
            "validation-negative": ForeignNewsPrediction("NEGATIVE", 1.0),
            "validation-neutral": ForeignNewsPrediction("NEUTRAL", 1.0),
            "validation-positive": ForeignNewsPrediction("POSITIVE", 1.0),
        },
        fail_on_test=True,
    )
    harness = ForeignNewsEvaluationHarness(clock_ns=_clock(step_ns=1_000_000))
    candidates = (
        ForeignNewsLocalCandidate(MODEL_CANDIDATES[0], prosus, footprint_bytes=100),
        ForeignNewsLocalCandidate(MODEL_CANDIDATES[1], tone, footprint_bytes=200),
        ForeignNewsLocalCandidate(MODEL_CANDIDATES[2], baseline, footprint_bytes=300),
    )

    selection = harness.evaluate_validation(
        selection_id="fns_evaluation_000001",
        selection_generation=1,
        candidates=candidates,
        examples=_validation_examples(),
    )

    assert selection.candidate_models == MODEL_CANDIDATES
    assert selection.selected_model == "PROSUSAI_FINBERT"
    assert prosus.call_count == tone.call_count == baseline.call_count == 3

    tested = harness.evaluate_selected_test(
        selection=selection,
        candidates=candidates,
        examples=(
            ForeignNewsEvaluationExample(
                text="test-critical-negative",
                expected_label="NEGATIVE",
                critical_negation_number_unit=True,
            ),
            ForeignNewsEvaluationExample(text="test-neutral", expected_label="NEUTRAL"),
            ForeignNewsEvaluationExample(text="test-positive", expected_label="POSITIVE"),
        ),
    )

    assert tested.metrics.critical_negation_number_unit_errors == 1
    assert tested.selection.selection_status == "ABSTAIN"
    assert tested.selection.abstain_reason == "TEST_FAILED"
    assert prosus.call_count == 6
    assert tone.call_count == baseline.call_count == 3


def test_validation_rejects_dataset_without_every_required_class() -> None:
    harness = ForeignNewsEvaluationHarness(clock_ns=_clock(step_ns=1))
    candidate = ForeignNewsLocalCandidate(
        MODEL_CANDIDATES[0],
        _Classifier(
            {
                "only-negative-1": ForeignNewsPrediction("NEGATIVE", 1.0),
                "only-negative-2": ForeignNewsPrediction("NEGATIVE", 1.0),
                "only-negative-3": ForeignNewsPrediction("NEGATIVE", 1.0),
            }
        ),
        footprint_bytes=1,
    )

    with pytest.raises(
        ForeignNewsModelSelectionError, match="FOREIGN_NEWS_EVALUATION_CLASS_COVERAGE"
    ):
        harness.evaluate_candidate(
            candidate=candidate,
            examples=(
                ForeignNewsEvaluationExample(text="only-negative-1", expected_label="NEGATIVE"),
                ForeignNewsEvaluationExample(text="only-negative-2", expected_label="NEGATIVE"),
                ForeignNewsEvaluationExample(text="only-negative-3", expected_label="NEGATIVE"),
            ),
        )


def test_selected_test_cannot_be_repeated_or_swap_the_selected_candidate() -> None:
    harness = ForeignNewsEvaluationHarness(clock_ns=_clock(step_ns=1))
    candidates = (
        ForeignNewsLocalCandidate(
            MODEL_CANDIDATES[0],
            _Classifier(
                {
                    "validation-negative": ForeignNewsPrediction("NEGATIVE", 1.0),
                    "validation-neutral": ForeignNewsPrediction("NEUTRAL", 1.0),
                    "validation-positive": ForeignNewsPrediction("POSITIVE", 1.0),
                }
            ),
            footprint_bytes=1,
        ),
        ForeignNewsLocalCandidate(
            MODEL_CANDIDATES[1],
            _Classifier(
                {
                    "validation-negative": ForeignNewsPrediction("NEGATIVE", 1.0),
                    "validation-neutral": ForeignNewsPrediction("NEUTRAL", 1.0),
                    "validation-positive": ForeignNewsPrediction("POSITIVE", 1.0),
                }
            ),
            footprint_bytes=2,
        ),
        ForeignNewsLocalCandidate(
            MODEL_CANDIDATES[2],
            _Classifier(
                {
                    "validation-negative": ForeignNewsPrediction("NEGATIVE", 1.0),
                    "validation-neutral": ForeignNewsPrediction("NEUTRAL", 1.0),
                    "validation-positive": ForeignNewsPrediction("POSITIVE", 1.0),
                }
            ),
            footprint_bytes=3,
        ),
    )
    selection = harness.evaluate_validation(
        selection_id="fns_evaluation_000002",
        selection_generation=2,
        candidates=candidates,
        examples=_validation_examples(),
    )
    completed = harness.evaluate_selected_test(
        selection=selection,
        candidates=candidates,
        examples=_validation_examples(),
    ).selection

    with pytest.raises(ForeignNewsModelSelectionError, match="FOREIGN_NEWS_TEST_ALREADY_EVALUATED"):
        harness.evaluate_selected_test(
            selection=completed,
            candidates=candidates,
            examples=_validation_examples(),
        )


def _validation_examples() -> tuple[ForeignNewsEvaluationExample, ...]:
    return (
        ForeignNewsEvaluationExample(text="validation-negative", expected_label="NEGATIVE"),
        ForeignNewsEvaluationExample(text="validation-neutral", expected_label="NEUTRAL"),
        ForeignNewsEvaluationExample(text="validation-positive", expected_label="POSITIVE"),
    )


class _Classifier:
    def __init__(
        self,
        predictions: dict[str, ForeignNewsPrediction],
        *,
        fail_on_test: bool = False,
    ) -> None:
        self._predictions = predictions
        self._fail_on_test = fail_on_test
        self.call_count = 0

    def predict(self, text: str) -> ForeignNewsPrediction:
        self.call_count += 1
        if self._fail_on_test and text.startswith("test-"):
            raise AssertionError("unselected candidate must not run against test data")
        return self._predictions[text]


def _clock(*, step_ns: int) -> Callable[[], int]:
    value = 0

    def now() -> int:
        nonlocal value
        current = value
        value += step_ns
        return current

    return now
