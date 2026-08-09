from __future__ import annotations

import csv
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from app.cross_market.foreign_news import MODEL_CANDIDATES
from app.cross_market.foreign_news_evaluator import (
    ForeignNewsEvaluationExample,
    ForeignNewsEvaluationHarness,
    ForeignNewsLocalCandidate,
    ForeignNewsPrediction,
)
from app.cross_market.foreign_news_local_evaluation import (
    ForeignNewsLocalEvaluationInputs,
    _load_loughran_mcdonald_candidate,
    load_sentivent_gold_split,
    load_tfns_stress_split,
    run_local_model_selection,
)
from app.cross_market.foreign_news_evaluation_cli import (
    _TEST_RESERVATION_CONTRACT_ID,
    _TEST_RESERVATION_NAME,
    ForeignNewsEvaluationCliError,
    _evaluate_once,
    _load_receipt_if_present,
    _write_new_receipt,
)


def test_sentivent_loader_keeps_only_unambiguous_three_class_event_polarity(tmp_path: Path) -> None:
    dataset_root = tmp_path / "sentivent"
    split_path = dataset_root / "data" / "sentivent_unified_sentence" / "validation.jsonl"
    split_path.parent.mkdir(parents=True)
    (dataset_root / "metadata").mkdir()
    (dataset_root / "metadata" / "build_info.json").write_text('{"revision":"test"}\n', encoding="utf-8")
    (dataset_root / "LICENSE").write_text("CC-BY-4.0\n", encoding="utf-8")
    rows = (
        _sentivent_row("good outlook", ("positive",)),
        _sentivent_row("losses widen", ("negative",)),
        _sentivent_row("guidance unchanged", ("neutral",)),
        _sentivent_row("mixed result", ("positive", "negative")),
        {"annotations": [], "text": "unlabeled"},
    )
    split_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    loaded = load_sentivent_gold_split(dataset_root=dataset_root, split="validation")

    assert [item.expected_label for item in loaded.examples] == ["POSITIVE", "NEGATIVE", "NEUTRAL"]
    assert loaded.receipt.included_example_count == 3
    assert loaded.receipt.excluded_ambiguous_or_unlabeled_count == 2
    assert len(loaded.receipt.raw_sha256) == 64
    assert "good outlook" not in str(loaded.receipt.to_payload())


def test_tfns_loader_maps_bearish_bullish_and_neutral_without_retaining_text(tmp_path: Path) -> None:
    dataset_root = tmp_path / "tfns"
    dataset_root.mkdir()
    (dataset_root / "README.md").write_text("MIT\n", encoding="utf-8")
    path = dataset_root / "sent_valid.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("text", "label"))
        writer.writeheader()
        writer.writerows(
            (
                {"text": "bearish market", "label": "0"},
                {"text": "bullish market", "label": "1"},
                {"text": "flat market", "label": "2"},
            )
        )

    loaded = load_tfns_stress_split(dataset_root=dataset_root)

    assert [item.expected_label for item in loaded.examples] == ["NEGATIVE", "POSITIVE", "NEUTRAL"]
    assert loaded.receipt.included_example_count == 3
    assert "bullish market" not in str(loaded.receipt.to_payload())


def test_loughran_mcdonald_master_dictionary_uses_nonzero_year_membership(tmp_path: Path) -> None:
    dictionary_path = tmp_path / "loughran-mcdonald-master-dictionary.csv"
    dictionary_path.write_text(
        "Word,Negative,Positive\n"
        "LOSS,2009,0\n"
        "GAIN,0,2009\n"
        "FLAT,0,0\n",
        encoding="utf-8",
    )

    classifier, receipt = _load_loughran_mcdonald_candidate(dictionary_path)

    assert classifier.predict("loss").label == "NEGATIVE"
    assert classifier.predict("gain").label == "POSITIVE"
    assert classifier.predict("flat").label == "NEUTRAL"
    assert receipt.candidate_model == "LOUGHRAN_MCDONALD_BASELINE"


def test_local_selection_runs_blind_and_stress_only_for_the_validation_winner() -> None:
    counters = {candidate: 0 for candidate in MODEL_CANDIDATES}
    candidates = tuple(
        ForeignNewsLocalCandidate(
            candidate_model=candidate,
            classifier=_Classifier(candidate_model=candidate, counters=counters, wrong=False),
            footprint_bytes=index + 1,
        )
        for index, candidate in enumerate(MODEL_CANDIDATES)
    )
    loaded_blind = 0
    loaded_stress = 0
    execution_order: list[str] = []

    def reserve_selected_test(_: object) -> None:
        execution_order.append("reserved")

    def blind_loader() -> tuple[ForeignNewsEvaluationExample, ...]:
        nonlocal loaded_blind
        assert execution_order == ["reserved"]
        loaded_blind += 1
        return _examples(prefix="blind")

    def stress_loader() -> tuple[ForeignNewsEvaluationExample, ...]:
        nonlocal loaded_stress
        loaded_stress += 1
        return _examples(prefix="stress")

    result = run_local_model_selection(
        inputs=ForeignNewsLocalEvaluationInputs(
            candidates=candidates,
                validation_examples=_examples(prefix="validation"),
                blind_test_loader=blind_loader,
                before_blind_test=reserve_selected_test,
                tfns_stress_loader=stress_loader,
            harness=ForeignNewsEvaluationHarness(clock_ns=_clock(step_ns=1_000_000)),
        ),
        selection_id="fns_local_eval_000001",
        selection_generation=1,
    )

    assert result.selection.selection_status == "TEST_EVALUATED"
    assert result.selection.selected_model == "PROSUSAI_FINBERT"
    assert result.blind_test_metrics is not None
    assert result.tfns_stress_metrics is not None
    assert loaded_blind == loaded_stress == 1
    assert execution_order == ["reserved"]
    assert counters == {
        "PROSUSAI_FINBERT": 9,
        "YIYANGHKUST_FINBERT_TONE": 3,
        "LOUGHRAN_MCDONALD_BASELINE": 3,
    }


def test_validation_abstain_never_loads_blind_test_or_tfns_stress() -> None:
    candidates = tuple(
        ForeignNewsLocalCandidate(
            candidate_model=candidate,
            classifier=_Classifier(candidate_model=candidate, counters={}, wrong=True),
            footprint_bytes=index + 1,
        )
        for index, candidate in enumerate(MODEL_CANDIDATES)
    )

    def forbidden_loader() -> tuple[ForeignNewsEvaluationExample, ...]:
        raise AssertionError("validation abstain must not consume test or stress input")

    result = run_local_model_selection(
        inputs=ForeignNewsLocalEvaluationInputs(
            candidates=candidates,
            validation_examples=_examples(prefix="validation"),
            blind_test_loader=forbidden_loader,
            tfns_stress_loader=forbidden_loader,
            harness=ForeignNewsEvaluationHarness(clock_ns=_clock(step_ns=1_000_000)),
        ),
        selection_id="fns_local_eval_000002",
        selection_generation=1,
    )

    assert result.selection.selection_status == "ABSTAIN"
    assert result.selection.test_evaluation_count == 0
    assert result.blind_test_metrics is None
    assert result.tfns_stress_metrics is None


def test_local_receipt_is_content_free_single_create_and_can_be_reloaded(tmp_path: Path) -> None:
    receipt_path = tmp_path / "sentivent-gold-plus-tfns-stress.v1.json"
    payload: dict[str, object] = {
        "contractId": "foreign-news-local-evaluation-receipt-v1",
        "evaluationInputDigest": "a" * 64,
        "modelArtifacts": [{}, {}, {}],
        "result": {
            "blindTest": None,
            "selection": {"selectionStatus": "ABSTAIN", "testEvaluationCount": 0},
            "tfnsStress": None,
        },
        "sentiventBlindTest": None,
        "sentiventValidation": {"rawSha256": "b" * 64},
        "tfnsStress": None,
    }

    _write_new_receipt(receipt_path, payload)

    assert _load_receipt_if_present(receipt_path) == payload
    assert oct(receipt_path.stat().st_mode & 0o777) == "0o600"
    assert "text" not in receipt_path.read_text(encoding="utf-8").casefold()
    with pytest.raises(ForeignNewsEvaluationCliError, match="FOREIGN_NEWS_EVALUATION_RECEIPT_EXISTS"):
        _write_new_receipt(receipt_path, payload)


def test_stale_test_reservation_blocks_evaluation_before_any_dataset_is_read(tmp_path: Path) -> None:
    evaluation_root = tmp_path / "finbert-eval"
    evaluation_root.mkdir(mode=0o700)
    receipts = evaluation_root / "receipts"
    receipts.mkdir(mode=0o700)
    reservation = receipts / _TEST_RESERVATION_NAME
    reservation.write_text(
        json.dumps(
            {
                "contractId": _TEST_RESERVATION_CONTRACT_ID,
                "evaluationInputDigest": "a" * 64,
                "selectedModel": "PROSUSAI_FINBERT",
                "state": "TEST_RESERVED",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    reservation.chmod(0o600)

    with pytest.raises(ForeignNewsEvaluationCliError, match="FOREIGN_NEWS_TEST_EVALUATION_RESUME_BLOCKED"):
        _evaluate_once(evaluation_root=evaluation_root)


class _Classifier:
    def __init__(self, *, candidate_model: str, counters: dict[str, int], wrong: bool) -> None:
        self._candidate_model = candidate_model
        self._counters = counters
        self._wrong = wrong

    def predict(self, text: str) -> ForeignNewsPrediction:
        self._counters[self._candidate_model] = self._counters.get(self._candidate_model, 0) + 1
        expected = text.rsplit("-", maxsplit=1)[-1].upper()
        if self._wrong:
            expected = "POSITIVE" if expected != "POSITIVE" else "NEGATIVE"
        return ForeignNewsPrediction(expected, 1.0)


def _examples(*, prefix: str) -> tuple[ForeignNewsEvaluationExample, ...]:
    return tuple(
        ForeignNewsEvaluationExample(
            text=f"{prefix}-{label.casefold()}",
            expected_label=label,
            critical_negation_number_unit=label == "NEGATIVE",
        )
        for label in ("NEGATIVE", "NEUTRAL", "POSITIVE")
    )


def _sentivent_row(text: str, polarities: tuple[str, ...]) -> dict[str, object]:
    return {
        "annotations": [
            {
                "kind": "event",
                "scoped_polarity": polarity,
            }
            for polarity in polarities
        ],
        "text": text,
    }


def _clock(*, step_ns: int) -> Callable[[], int]:
    value = 0

    def now() -> int:
        nonlocal value
        current = value
        value += step_ns
        return current

    return now
