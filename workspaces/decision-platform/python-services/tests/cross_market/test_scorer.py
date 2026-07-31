from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.cross_market.scorer import (
    AdverseDirection,
    ComponentName,
    CrossMarketScorer,
    ScorerSeries,
)


EVALUATED_AT = datetime(2026, 7, 31, 0, 30, tzinfo=UTC)


def _series(
    instrument: str,
    component: ComponentName,
    current: Decimal | None,
    *,
    direction: AdverseDirection = AdverseDirection.HIGH,
    history: tuple[Decimal, ...] | None = None,
    complete: bool = True,
    available_at: datetime | None = None,
) -> ScorerSeries:
    return ScorerSeries(
        instrument=instrument,
        component=component,
        adverse_direction=direction,
        current_value=current,
        current_complete=complete,
        current_available_at=available_at or EVALUATED_AT - timedelta(minutes=1),
        completed_history=history
        or tuple(Decimal(value) for value in range(1, 253)),
    )


def _complete_input() -> tuple[ScorerSeries, ...]:
    negative_history = tuple(Decimal(-value) for value in range(1, 253))
    return (
        _series("NVDA", ComponentName.SEMICONDUCTOR, Decimal("253")),
        _series("MU", ComponentName.SEMICONDUCTOR, Decimal("200")),
        _series("AMD", ComponentName.SEMICONDUCTOR, Decimal("150")),
        _series("ASML", ComponentName.SEMICONDUCTOR, Decimal("100")),
        _series(
            "QQQ",
            ComponentName.BROAD_MARKET,
            Decimal("-253"),
            direction=AdverseDirection.LOW,
            history=negative_history,
        ),
        _series(
            "SPY",
            ComponentName.BROAD_MARKET,
            Decimal("-199"),
            direction=AdverseDirection.LOW,
            history=negative_history,
        ),
        _series("USDKRW", ComponentName.FX, Decimal("253")),
        _series("MARGIN_CREDIT", ComponentName.DOMESTIC_AMPLIFICATION, Decimal("253")),
        _series("SHORT_SELLING", ComponentName.DOMESTIC_AMPLIFICATION, Decimal("200")),
        _series("STOCK_LOAN", ComponentName.DOMESTIC_AMPLIFICATION, Decimal("150")),
    )


def test_scorer_uses_exact_252_adverse_percentiles_and_equal_weight_median() -> None:
    result = CrossMarketScorer("cross-market-score.v1").score(
        _complete_input(), evaluated_at=EVALUATED_AT
    )

    assert result.available is True
    assert result.component(ComponentName.SEMICONDUCTOR).score == Decimal("69.246032")
    assert result.component(ComponentName.BROAD_MARKET).score == Decimal("89.384921")
    assert result.component(ComponentName.FX).score == Decimal("100.000000")
    assert result.component(ComponentName.DOMESTIC_AMPLIFICATION).score == Decimal(
        "79.166667"
    )
    assert all(item.history_session_count == 252 for item in result.components)
    assert b"threshold" not in result.canonical_bytes()
    assert b"decision" not in result.canonical_bytes()


def test_scorer_is_byte_identical_under_input_reordering_and_changes_on_mutation() -> None:
    scorer = CrossMarketScorer("cross-market-score.v1")
    inputs = _complete_input()
    first = scorer.score(inputs, evaluated_at=EVALUATED_AT).canonical_bytes()
    reordered = scorer.score(tuple(reversed(inputs)), evaluated_at=EVALUATED_AT).canonical_bytes()
    mutated = list(inputs)
    mutated[0] = _series("NVDA", ComponentName.SEMICONDUCTOR, Decimal("1"))

    assert first == reordered
    assert first != scorer.score(tuple(mutated), evaluated_at=EVALUATED_AT).canonical_bytes()


def test_ties_are_midrank_and_missing_future_incomplete_nonfinite_are_unavailable_not_zero() -> None:
    tied = tuple(Decimal("7") for _ in range(252))
    valid_tie = _series(
        "USDKRW",
        ComponentName.FX,
        Decimal("7"),
        history=tied,
    )
    invalid = (
        _series("NVDA", ComponentName.SEMICONDUCTOR, None),
        _series(
            "MU",
            ComponentName.SEMICONDUCTOR,
            Decimal("20"),
            complete=False,
        ),
        _series(
            "AMD",
            ComponentName.SEMICONDUCTOR,
            Decimal("20"),
            available_at=EVALUATED_AT + timedelta(seconds=1),
        ),
        _series("ASML", ComponentName.SEMICONDUCTOR, Decimal("NaN")),
        valid_tie,
    )

    result = CrossMarketScorer("cross-market-score.v1").score(
        invalid, evaluated_at=EVALUATED_AT
    )

    assert result.component(ComponentName.FX).score == Decimal("50.000000")
    semiconductor = result.component(ComponentName.SEMICONDUCTOR)
    assert semiconductor.available is False
    assert semiconductor.score is None
    assert semiconductor.reason == "INSUFFICIENT_COVERAGE"
    assert result.available is False


def test_wrong_history_length_and_infinity_never_become_fake_available() -> None:
    result = CrossMarketScorer("cross-market-score.v1").score(
        (
            _series(
                "USDKRW",
                ComponentName.FX,
                Decimal("Infinity"),
            ),
            _series(
                "QQQ",
                ComponentName.BROAD_MARKET,
                Decimal("10"),
                history=tuple(Decimal(value) for value in range(251)),
            ),
        ),
        evaluated_at=EVALUATED_AT,
    )

    assert all(component.score is None for component in result.components)
    assert result.available is False
