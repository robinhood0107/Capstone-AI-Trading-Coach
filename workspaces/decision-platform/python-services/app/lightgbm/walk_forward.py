"""S5.2 exact expanding walk-forward, purge, embargo와 final-test access guard."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Generic, TypeVar

from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.labels import LabelRow
from app.lightgbm.pit_calendar import ELIGIBLE_SESSION_COUNT

INITIAL_FIT = 504
EMBARGO = 5
EARLY = 21
CALIBRATION = 21
EVALUATION = 63
FINAL_TEST = 126
PRIMARY_FOLDS = 3
# eligible session 수는 달력 정책이 유도한다. 여기서 다시 선언하면 두 곳이 어긋난다.
EXPECTED_SESSIONS = ELIGIBLE_SESSION_COUNT


@dataclass(frozen=True)
class FoldSplit:
    """한 fold의 purged expanding fit과 독립 early/calibration/evaluation blocks."""

    name: str
    fit_sessions: tuple[date, ...]
    purged_sessions: tuple[date, ...]
    embargo_sessions: tuple[date, ...]
    early_sessions: tuple[date, ...]
    calibration_sessions: tuple[date, ...]
    evaluation_sessions: tuple[date, ...]


@dataclass(frozen=True)
class WalkForwardPlan:
    """세 primary folds와 untouched final split을 모두 포함한 exact schedule."""

    folds: tuple[FoldSplit, ...]
    final: FoldSplit


def build_walk_forward_plan(
    sessions: Sequence[date],
    label_rows: Sequence[LabelRow],
) -> WalkForwardPlan:
    """sessionDate block 단위로 1,007-session expanding plan을 만들고 label overlap을 제거한다."""

    ordered = tuple(sessions)
    if len(ordered) != EXPECTED_SESSIONS or ordered != tuple(sorted(set(ordered))):
        raise LightGbmContractError("walk-forward requires exact 1,007 unique sorted sessions")
    interval_end_by_session: dict[date, date] = {}
    for row in label_rows:
        previous = interval_end_by_session.get(row.session_date)
        if previous is None or row.interval_end > previous:
            interval_end_by_session[row.session_date] = row.interval_end

    folds: list[FoldSplit] = []
    history_end = INITIAL_FIT
    for number in range(1, PRIMARY_FOLDS + 1):
        fold, history_end = _build_split(
            ordered,
            interval_end_by_session,
            history_end=history_end,
            evaluation_size=EVALUATION,
            name=f"fold-{number}",
        )
        folds.append(fold)
    final, history_end = _build_split(
        ordered,
        interval_end_by_session,
        history_end=history_end,
        evaluation_size=FINAL_TEST,
        name="final",
    )
    if history_end != len(ordered):
        raise LightGbmContractError("walk-forward blocks do not consume exact eligible sessions")
    plan = WalkForwardPlan(tuple(folds), final)
    validate_zero_overlap(plan, label_rows)
    return plan


def _build_split(
    sessions: tuple[date, ...],
    interval_end_by_session: Mapping[date, date],
    *,
    history_end: int,
    evaluation_size: int,
    name: str,
) -> tuple[FoldSplit, int]:
    embargo = sessions[history_end : history_end + EMBARGO]
    early_start = history_end + EMBARGO
    early = sessions[early_start : early_start + EARLY]
    calibration_start = early_start + EARLY
    calibration = sessions[calibration_start : calibration_start + CALIBRATION]
    evaluation_start = calibration_start + CALIBRATION
    evaluation = sessions[evaluation_start : evaluation_start + evaluation_size]
    if not (
        len(embargo) == EMBARGO
        and len(early) == EARLY
        and len(calibration) == CALIBRATION
        and len(evaluation) == evaluation_size
    ):
        raise LightGbmContractError(f"{name} split is incomplete")

    fit_candidates = sessions[:history_end]
    first_non_embargo = early[0]
    purged = tuple(
        session
        for session in fit_candidates
        if interval_end_by_session.get(session, session) >= first_non_embargo
    )
    purged_set = set(purged)
    fit = tuple(session for session in fit_candidates if session not in purged_set)
    return (
        FoldSplit(name, fit, purged, embargo, early, calibration, evaluation),
        evaluation_start + evaluation_size,
    )


def validate_zero_overlap(plan: WalkForwardPlan, label_rows: Sequence[LabelRow]) -> None:
    """fit label interval과 early/calibration/evaluation/test block의 교집합이 0인지 전수 검사한다."""

    rows_by_session: dict[date, list[LabelRow]] = {}
    for row in label_rows:
        rows_by_session.setdefault(row.session_date, []).append(row)
    for split in (*plan.folds, plan.final):
        future = {*split.early_sessions, *split.calibration_sessions, *split.evaluation_sessions}
        for session in split.fit_sessions:
            for row in rows_by_session.get(
                session, ()
            ):  # 같은 sessionDate의 모든 symbol을 함께 검사한다.
                if any(
                    row.interval_start <= future_session <= row.interval_end
                    for future_session in future
                ):
                    raise LightGbmContractError(
                        f"{split.name} label interval overlaps a future block"
                    )
        blocks = (
            set(split.fit_sessions),
            set(split.embargo_sessions),
            set(split.early_sessions),
            set(split.calibration_sessions),
            set(split.evaluation_sessions),
        )
        for left, block in enumerate(blocks):
            for other in blocks[left + 1 :]:
                if block & other:
                    raise LightGbmContractError(f"{split.name} session blocks overlap")


def split_visualization(plan: WalkForwardPlan) -> dict[str, object]:
    """outcome을 제외하고 session/block/label-interval policy만 표시할 deterministic view를 만든다."""

    def block(split: FoldSplit) -> dict[str, object]:
        return {
            "name": split.name,
            "fit": _bounds(split.fit_sessions),
            "purged": _bounds(split.purged_sessions),
            "embargo": _bounds(split.embargo_sessions),
            "early": _bounds(split.early_sessions),
            "calibration": _bounds(split.calibration_sessions),
            "evaluationOrTest": _bounds(split.evaluation_sessions),
            "labelInterval": "[open(t+1),open(t+6)]",
        }

    return {"folds": [block(split) for split in plan.folds], "final": block(plan.final)}


def _bounds(values: tuple[date, ...]) -> dict[str, object]:
    return {
        "start": values[0].isoformat() if values else None,
        "end": values[-1].isoformat() if values else None,
        "count": len(values),
    }


T = TypeVar("T")


class UntouchedTestLoader(Generic[T]):
    """tuning 중 접근을 거부하고 final report 단계에서 정확히 한 번만 payload를 연다."""

    def __init__(self, payload: T, *, factory: Callable[[], T] | None = None) -> None:
        self._payload = payload
        self._factory = factory
        self._access_count = 0

    @classmethod
    def deferred(cls, factory: Callable[[], T]) -> UntouchedTestLoader[T]:
        """선택·reservation 전에는 final projection callback 자체를 호출하지 않는다."""

        return cls(None, factory=factory)  # type: ignore[arg-type]

    @property
    def access_count(self) -> int:
        return self._access_count

    def read(self, *, phase: str) -> T:
        """phase가 FINAL_REPORT일 때 첫 접근만 허용한다."""

        if phase != "FINAL_REPORT":
            raise LightGbmContractError("untouched final test cannot be read during tuning")
        if self._access_count != 0:
            raise LightGbmContractError("untouched final test may be read exactly once")
        self._access_count += 1
        return self._factory() if self._factory is not None else self._payload
