"""S5.4 weekly manual drift evaluation과 immediate ABSTAIN state."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.metrics import CalibrationMetrics


REASON_PRECEDENCE = (
    "ARTIFACT_DRIFT",
    "STALE_EVIDENCE",
    "CALIBRATION_FAILED",
    "UNIDENTIFIABLE_OUTPUT",
    "MISSING_EVIDENCE",
    "PRODUCER_FAILED",
)


@dataclass(frozen=True)
class DriftBaseline:
    """선택 후보의 세 evaluation fold 평균 ECE/Brier."""

    validation_ece: float
    validation_brier: float


@dataclass(frozen=True)
class DriftState:
    """연속 ECE breach 수와 현재 즉시 abstention 사유."""

    consecutive_ece_breaches: int = 0
    abstain_reason: str | None = None


def baseline_from_folds(folds: Sequence[CalibrationMetrics]) -> DriftBaseline:
    """정확히 세 primary evaluation fold에서 drift baseline 평균을 만든다."""

    if len(folds) != 3:
        raise LightGbmContractError("drift baseline requires exactly three evaluation folds")
    return DriftBaseline(
        validation_ece=sum(item.ece for item in folds) / 3.0,
        validation_brier=sum(item.brier for item in folds) / 3.0,
    )


def evaluate_weekly_drift(
    previous: DriftState,
    *,
    baseline: DriftBaseline,
    current: CalibrationMetrics | None,
    mature_sessions: int,
    present_classes: frozenset[int],
    new_production_model_activated: bool = False,
) -> DriftState:
    """63-session window를 평가하며 Brier는 한 번, ECE는 두 번 연속 breach 시 ABSTAIN한다."""

    if new_production_model_activated:
        previous = DriftState()
    if mature_sessions < 30 or present_classes != frozenset({0, 1, 2}) or current is None:
        return DriftState(previous.consecutive_ece_breaches, "UNIDENTIFIABLE_OUTPUT")
    observed = (
        baseline.validation_ece,
        baseline.validation_brier,
        current.ece,
        current.brier,
    )
    if not all(math.isfinite(value) for value in observed):
        return DriftState(previous.consecutive_ece_breaches, "UNIDENTIFIABLE_OUTPUT")
    if min(observed) < 0:
        raise LightGbmContractError("drift metrics must be non-negative")

    brier_breached = (
        current.brier > 0
        if baseline.validation_brier == 0
        else current.brier > 1.2 * baseline.validation_brier
    )
    if brier_breached:
        return DriftState(previous.consecutive_ece_breaches, "ARTIFACT_DRIFT")

    threshold = max(0.10, baseline.validation_ece + 0.05)
    if current.ece > threshold:
        count = previous.consecutive_ece_breaches + 1
        return DriftState(count, "ARTIFACT_DRIFT" if count >= 2 else None)
    # passing evaluation만 consecutive counter를 reset한다.
    return DriftState(0, None)


def highest_precedence_reason(reasons: Sequence[str]) -> str | None:
    """동시에 발생한 abstention 원인에서 locked precedence가 가장 높은 값을 고른다."""

    unknown = set(reasons) - set(REASON_PRECEDENCE)
    if unknown:
        raise LightGbmContractError("unknown Signal ABSTAIN reason")
    return next((reason for reason in REASON_PRECEDENCE if reason in reasons), None)
