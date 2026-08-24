from __future__ import annotations

import hashlib

import numpy as np
import pytest

import app.lightgbm.training as training_module
from app.lightgbm.calibration import fit_ovr_platt
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.metrics import (
    CalibrationMetrics,
    class_reliability,
    multiclass_brier,
    natural_log_loss,
    top_label_ece,
)
from app.lightgbm.training import (
    Candidate,
    CandidateEvaluation,
    FinalFitArrays,
    FoldArrays,
    FoldEvaluation,
    capped_balanced_weights,
    exact_grid,
    fit_lightgbm_reproducible,
    model_manifest_bytes,
    open_final_test_after_selection,
    raw_margins,
    research_cost_report,
    resolve_deterministic_fit,
    run_exact_four_grid,
    run_final_candidate,
    select_candidate,
)
from app.lightgbm.walk_forward import UntouchedTestLoader


def test_exact_four_candidate_count_and_order() -> None:
    assert [(item.num_leaves, item.class_weight) for item in exact_grid()] == [
        (15, "NONE"),
        (15, "CAPPED_BALANCED"),
        (31, "NONE"),
        (31, "CAPPED_BALANCED"),
    ]


def test_capped_balanced_formula_cap_and_mean_normalization() -> None:
    labels = np.asarray([0] * 98 + [1] + [2], dtype=np.int64)
    weights = capped_balanced_weights(labels)
    raw = np.asarray([100 / (3 * 98), 5.0, 5.0])
    expected = raw[labels] / raw[labels].mean()
    np.testing.assert_allclose(weights, expected)
    assert weights.mean() == pytest.approx(1.0)
    with pytest.raises(LightGbmContractError, match="UNIDENTIFIABLE_OUTPUT"):
        capped_balanced_weights(np.asarray([0, 1, 0, 1]))


def test_platt_solver_renormalizes_and_rejects_degenerate_class() -> None:
    margins = np.asarray(
        [
            [3.0, -1.0, -2.0],
            [2.0, 0.0, -1.0],
            [-2.0, 3.0, -1.0],
            [-1.0, 2.0, 0.0],
            [-2.0, -1.0, 3.0],
            [0.0, -1.0, 2.0],
        ],
        dtype=np.float64,
    )
    labels = np.asarray([0, 0, 1, 1, 2, 2], dtype=np.int64)
    calibrator = fit_ovr_platt(margins, labels)
    probabilities = calibrator.transform(margins)
    np.testing.assert_allclose(probabilities.sum(axis=1), 1.0, rtol=0.0, atol=1e-15)
    assert np.isfinite(probabilities).all()
    assert calibrator.canonical_bytes() == calibrator.canonical_bytes()
    with pytest.raises(LightGbmContractError, match="UNIDENTIFIABLE_OUTPUT"):
        fit_ovr_platt(margins[:4], np.asarray([0, 0, 1, 1]))


def test_ece_boundaries_and_unscaled_multiclass_brier() -> None:
    class_zero = np.asarray([index / 10 for index in range(11)], dtype=np.float64)
    probabilities = np.column_stack((class_zero, (1 - class_zero) / 2, (1 - class_zero) / 2))
    labels = np.asarray([0] * 11)
    reliability = class_reliability(labels, probabilities)[0]
    assert [row[0] for row in reliability] == [1, 1, 1, 1, 1, 1, 1, 1, 1, 2]

    opposite = np.asarray([[0.0, 0.0, 1.0]])
    assert multiclass_brier(np.asarray([0]), opposite) == pytest.approx(2.0)
    assert natural_log_loss(np.asarray([2]), opposite) == pytest.approx(0.0)
    assert top_label_ece(np.asarray([2]), opposite) == pytest.approx(0.0)


def test_threads_four_stable_four_drift_to_one_stable_and_one_drift_fails() -> None:
    stable_calls: list[int] = []

    def stable(thread: int) -> tuple[bytes, int]:
        stable_calls.append(thread)
        return b"same", thread

    assert resolve_deterministic_fit(stable) == (b"same", 4, 4)
    assert stable_calls == [4, 4]

    counts = {4: 0, 1: 0}

    def fallback(thread: int) -> tuple[bytes, int]:
        counts[thread] += 1
        return (f"drift-{counts[thread]}".encode() if thread == 4 else b"single"), thread

    assert resolve_deterministic_fit(fallback) == (b"single", 1, 1)

    counts = {4: 0, 1: 0}

    def always_drift(thread: int) -> tuple[bytes, int]:
        counts[thread] += 1
        return f"{thread}-{counts[thread]}".encode(), thread

    with pytest.raises(LightGbmContractError, match="threads=1"):
        resolve_deterministic_fit(always_drift)


def _fold(passed: bool, score: float = 0.01) -> FoldEvaluation:
    raw = CalibrationMetrics(brier=0.2, log_loss=0.4, ece=0.1)
    calibrated = CalibrationMetrics(brier=0.19, log_loss=score, ece=0.04 if passed else 0.06)
    return FoldEvaluation(raw, calibrated, passed)


def test_candidate_selection_uses_all_folds_and_no_pass_skips_final_test() -> None:
    candidates = exact_grid()
    evaluations = [
        CandidateEvaluation(candidate, (_fold(False), _fold(False), _fold(False)))
        for candidate in candidates
    ]
    loader = UntouchedTestLoader("secret-test")
    selected, payload = open_final_test_after_selection(evaluations, loader)
    assert selected is None and payload is None and loader.access_count == 0

    evaluations[2] = CandidateEvaluation(
        candidates[2], (_fold(True, 0.3), _fold(True, 0.3), _fold(True, 0.3))
    )
    evaluations[3] = CandidateEvaluation(
        candidates[3], (_fold(True, 0.2), _fold(True, 0.2), _fold(True, 0.2))
    )
    assert select_candidate(evaluations) == evaluations[3]


def test_exact_grid_orchestrator_runs_twelve_folds_and_final_test_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, str]] = []
    dummy_model = training_module.TrainedBooster(object(), b"model", "a" * 64, 1, 1)  # type: ignore[arg-type]

    def fake_fit(
        x_fit: np.ndarray,
        y_fit: np.ndarray,
        x_early: np.ndarray,
        y_early: np.ndarray,
        candidate: Candidate,
    ) -> training_module.TrainedBooster:
        del x_fit, y_fit, x_early, y_early
        calls.append((candidate.num_leaves, candidate.class_weight))
        return dummy_model

    class _Calibrator:
        pass

    def fake_probabilities(
        model: object,
        calibrator: object,
        features: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        del model, calibrator
        labels = np.arange(len(features)) % 3
        probabilities = np.eye(3, dtype=np.float64)[labels]
        return probabilities, probabilities

    monkeypatch.setattr(training_module, "fit_lightgbm_reproducible", fake_fit)
    monkeypatch.setattr(
        training_module, "raw_margins", lambda model, features: np.zeros((len(features), 3))
    )
    monkeypatch.setattr(training_module, "fit_ovr_platt", lambda margins, labels: _Calibrator())
    monkeypatch.setattr(training_module, "calibrated_probabilities", fake_probabilities)

    def fold() -> FoldArrays:
        labels = np.tile(np.asarray([0, 1, 2]), 2)
        features = np.zeros((len(labels), 2), dtype=np.float32)
        return FoldArrays(features, labels, features, labels, features, labels, features, labels)

    runs = run_exact_four_grid([fold(), fold(), fold()])
    assert len(runs) == 4
    assert calls == [
        pair
        for pair in [
            (15, "NONE"),
            (15, "CAPPED_BALANCED"),
            (31, "NONE"),
            (31, "CAPPED_BALANCED"),
        ]
        for _ in range(3)
    ]

    labels = np.tile(np.asarray([0, 1, 2]), 2)
    features = np.zeros((len(labels), 2), dtype=np.float32)
    loader = UntouchedTestLoader((features, labels))
    final = run_final_candidate(
        runs,
        FinalFitArrays(features, labels, features, labels, features, labels),
        loader,
    )
    assert final is not None
    assert final.candidate == exact_grid()[0]
    assert loader.access_count == 1


def test_actual_lightgbm_text_and_manifest_hash_repeat() -> None:
    random = np.random.default_rng(20260729)
    x_fit = random.normal(size=(300, 6)).astype(np.float32)
    y_fit = np.tile(np.asarray([0, 1, 2], dtype=np.int64), 100)
    x_early = random.normal(size=(90, 6)).astype(np.float32)
    y_early = np.tile(np.asarray([0, 1, 2], dtype=np.int64), 30)
    candidate = exact_grid()[0]
    model = fit_lightgbm_reproducible(x_fit, y_fit, x_early, y_early, candidate)

    assert model.num_threads in {1, 4}
    assert model.model_sha256 == hashlib.sha256(model.model_text).hexdigest()
    assert raw_margins(model, x_early).shape == (90, 3)
    calibrator_sha = "a" * 64
    assert model_manifest_bytes(model, candidate, calibrator_sha) == model_manifest_bytes(
        model, candidate, calibrator_sha
    )


def test_cost_report_compares_full_always_hold_and_train_prior_baselines() -> None:
    labels = np.asarray([0, 1, 2, 0, 1, 2], dtype=np.int64)
    probabilities = np.eye(3, dtype=np.float64)[labels]
    report = research_cost_report(
        labels,
        probabilities,
        np.asarray([-0.01, 0.0, 0.01, -0.02, 0.0, 0.02]),
        np.asarray([2, 2, 2]),
    )
    for baseline in (report["alwaysHold"], report["trainOnlyPrior"]):
        assert isinstance(baseline, dict)
        assert {"logLoss", "brier", "ece", "macroF1", "confusionMatrix"} <= set(baseline)
        assert set(baseline["costSensitivityBps"]) == {"25", "30", "35"}
