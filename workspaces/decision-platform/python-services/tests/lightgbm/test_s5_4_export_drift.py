from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from app.lightgbm.drift import (
    DriftBaseline,
    DriftState,
    evaluate_weekly_drift,
    highest_precedence_reason,
)
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.export import (
    SignalArtifactIdentity,
    curated_contribution_report,
    export_signal_artifact,
    gain_importance,
)
from app.lightgbm.metrics import CalibrationMetrics
from app.lightgbm.training import TrainedBooster


class _ContributionBooster:
    def __init__(self, feature_names: list[str], break_additivity: bool = False) -> None:
        self._feature_names = feature_names
        self.break_additivity = break_additivity

    def feature_name(self) -> list[str]:
        return self._feature_names

    def feature_importance(
        self, importance_type: str = "split", iteration: int | None = None
    ) -> np.ndarray:
        assert importance_type == "gain" and iteration == 7
        return np.arange(1, len(self._feature_names) + 1, dtype=np.float64)

    def predict(self, data: np.ndarray, **kwargs: object) -> object:
        rows = len(data)
        raw = np.tile(np.asarray([[1.0, 2.0, 3.0]]), (rows, 1))
        if kwargs.get("raw_score"):
            return raw
        assert kwargs.get("pred_contrib")
        width = len(self._feature_names) + 1
        output = np.zeros((rows, width * 3), dtype=np.float64)
        for class_index in range(3):
            output[:, class_index * width + width - 1] = raw[:, class_index]
        if self.break_additivity:
            output[0, 0] = 1.0
        return output


def _identity(session: date, fixture: bool = True) -> SignalArtifactIdentity:
    return SignalArtifactIdentity(
        symbol="005930",
        session_date=session,
        evaluation_id="eval-005930-20260814",
        model_version="lgbm-v1-aaaaaaaaaaaa",
        model_report_id="mrp-bbbbbbbbbbbb",
        dataset_sha256="1" * 64,
        model_sha256="2" * 64,
        report_sha256="3" * 64,
        payload_sha256="4" * 64,
        provenance_sha256="5" * 64,
        fixture=fixture,
        provenance_class="FAKE_CONTRACT" if fixture else "PRODUCTION",
    )


def test_gain_importance_covers_every_feature() -> None:
    booster = _ContributionBooster(["a", "b", "c"])
    model = TrainedBooster(booster, b"model", "a" * 64, 7, 4)  # type: ignore[arg-type]
    assert gain_importance(model) == {"a": 1.0, "b": 2.0, "c": 3.0}


def test_contribution_caps_500_adds_to_margin_and_omits_identifying_keys() -> None:
    booster = _ContributionBooster(["a", "b"])
    features = np.zeros((600, 2), dtype=np.float64)
    row_keys = [(f"{index:06d}", date(2026, 1, 1) + timedelta(days=index)) for index in range(600)]
    payload = curated_contribution_report(
        booster,
        features,
        row_keys=row_keys,
        dataset_hash="d" * 64,
        feature_names=["a", "b"],
        best_iteration=7,
    )
    report = json.loads(payload)
    assert report["rowCount"] == 500
    assert all(set(row) == {"rowKeyHash", "classes"} for row in report["rows"])
    assert b"005930" not in payload and b"sessionDate" not in payload and b"symbol" not in payload

    with pytest.raises(LightGbmContractError, match="add"):
        curated_contribution_report(
            _ContributionBooster(["a", "b"], break_additivity=True),
            features[:1],
            row_keys=row_keys[:1],
            dataset_hash="d" * 64,
            feature_names=["a", "b"],
            best_iteration=7,
        )


def test_available_hold_has_calibrated_confidence_and_no_threshold_rule() -> None:
    session = date(2026, 8, 14)
    artifact = export_signal_artifact(
        _identity(session),
        as_of=datetime(2026, 8, 14, 6, 30, tzinfo=UTC),
        current_completed_session=session,
        calibrated_probabilities=np.asarray([0.2, 0.6, 0.2]),
        raw_margins=np.asarray([-1.0, 1.0, -1.0]),
    )
    assert artifact.payload["status"] == "AVAILABLE"
    assert artifact.payload["signal"] == "HOLD"
    assert artifact.payload["confidence"] == pytest.approx(0.6)
    assert "predictedReturn" not in artifact.payload
    assert "modelScore" not in artifact.payload
    assert artifact.model_score is not None


def test_stale_failure_and_missing_export_abstain_without_fabricated_values() -> None:
    session = date(2026, 8, 13)
    artifact = export_signal_artifact(
        _identity(session),
        as_of=datetime(2026, 8, 13, 6, 30, tzinfo=UTC),
        current_completed_session=date(2026, 8, 14),
        calibrated_probabilities=np.asarray([0.2, 0.6, 0.2]),
        raw_margins=np.asarray([-1.0, 1.0, -1.0]),
    )
    assert artifact.payload["status"] == "ABSTAIN"
    assert artifact.payload["reason"] == "STALE_EVIDENCE"
    for forbidden in ("asOf", "signal", "confidence", "predictedReturn"):
        assert forbidden not in artifact.payload
    assert artifact.model_score is None

    missing = export_signal_artifact(
        _identity(date(2026, 8, 14)),
        as_of=None,
        current_completed_session=date(2026, 8, 14),
        calibrated_probabilities=None,
        raw_margins=None,
    )
    assert missing.payload["reason"] == "MISSING_EVIDENCE"

    nonfinite = export_signal_artifact(
        _identity(date(2026, 8, 14)),
        as_of=datetime(2026, 8, 14, 6, 30, tzinfo=UTC),
        current_completed_session=date(2026, 8, 14),
        calibrated_probabilities=np.asarray([np.nan, 0.5, 0.5]),
        raw_margins=np.asarray([0.0, 0.0, 0.0]),
    )
    assert nonfinite.payload["reason"] == "UNIDENTIFIABLE_OUTPUT"


def test_drift_counter_immediate_brier_reset_and_unidentifiable_window() -> None:
    baseline = DriftBaseline(validation_ece=0.04, validation_brier=0.2)
    ece_breach = CalibrationMetrics(brier=0.2, log_loss=0.4, ece=0.11)
    first = evaluate_weekly_drift(
        DriftState(),
        baseline=baseline,
        current=ece_breach,
        mature_sessions=63,
        present_classes=frozenset({0, 1, 2}),
    )
    assert first == DriftState(1, None)
    second = evaluate_weekly_drift(
        first,
        baseline=baseline,
        current=ece_breach,
        mature_sessions=63,
        present_classes=frozenset({0, 1, 2}),
    )
    assert second == DriftState(2, "ARTIFACT_DRIFT")

    passing = evaluate_weekly_drift(
        second,
        baseline=baseline,
        current=CalibrationMetrics(brier=0.19, log_loss=0.3, ece=0.05),
        mature_sessions=63,
        present_classes=frozenset({0, 1, 2}),
    )
    assert passing == DriftState()
    brier = evaluate_weekly_drift(
        DriftState(),
        baseline=baseline,
        current=CalibrationMetrics(brier=0.241, log_loss=0.3, ece=0.05),
        mature_sessions=63,
        present_classes=frozenset({0, 1, 2}),
    )
    assert brier.abstain_reason == "ARTIFACT_DRIFT"
    zero_baseline = evaluate_weekly_drift(
        DriftState(),
        baseline=DriftBaseline(0.0, 0.0),
        current=CalibrationMetrics(brier=0.0001, log_loss=0.3, ece=0.0),
        mature_sessions=63,
        present_classes=frozenset({0, 1, 2}),
    )
    assert zero_baseline.abstain_reason == "ARTIFACT_DRIFT"
    insufficient = evaluate_weekly_drift(
        DriftState(),
        baseline=baseline,
        current=None,
        mature_sessions=29,
        present_classes=frozenset({0, 1}),
    )
    assert insufficient.abstain_reason == "UNIDENTIFIABLE_OUTPUT"
    nonfinite = evaluate_weekly_drift(
        DriftState(1),
        baseline=baseline,
        current=CalibrationMetrics(brier=np.nan, log_loss=0.3, ece=0.05),
        mature_sessions=63,
        present_classes=frozenset({0, 1, 2}),
    )
    assert nonfinite == DriftState(1, "UNIDENTIFIABLE_OUTPUT")


def test_reason_precedence_is_locked() -> None:
    assert (
        highest_precedence_reason(["PRODUCER_FAILED", "CALIBRATION_FAILED", "STALE_EVIDENCE"])
        == "STALE_EVIDENCE"
    )
