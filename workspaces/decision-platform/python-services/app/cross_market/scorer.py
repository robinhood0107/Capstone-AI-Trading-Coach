from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from typing import Final

_HISTORY_SESSIONS: Final[int] = 252
_SCORE_QUANTUM: Final[Decimal] = Decimal("0.000001")


class ComponentName(StrEnum):
    """S4.8A snapshot의 exact four component identity다."""

    SEMICONDUCTOR = "SEMICONDUCTOR"
    BROAD_MARKET = "BROAD_MARKET"
    FX = "FX"
    DOMESTIC_AMPLIFICATION = "DOMESTIC_AMPLIFICATION"


class AdverseDirection(StrEnum):
    """값이 커질수록 또는 작아질수록 adverse인지를 고정한다."""

    HIGH = "HIGH"
    LOW = "LOW"


@dataclass(frozen=True, slots=True)
class ScorerSeries:
    """I/O 없이 current가 252 completed sessions 분포에서 어디에 있는지 평가한다."""

    instrument: str
    component: ComponentName
    adverse_direction: AdverseDirection
    current_value: Decimal | None
    current_complete: bool
    current_available_at: datetime
    completed_history: tuple[Decimal, ...]


@dataclass(frozen=True, slots=True)
class ComponentScore:
    name: ComponentName
    available: bool
    score: Decimal | None
    source_instruments: tuple[str, ...]
    history_session_count: int
    reason: str | None

    def to_canonical(self) -> dict[str, object]:
        return {
            "available": self.available,
            "historySessionCount": self.history_session_count,
            "name": self.name.value,
            "reason": self.reason,
            "score": None if self.score is None else format(self.score, ".6f"),
            "sourceInstruments": list(self.source_instruments),
        }


@dataclass(frozen=True, slots=True)
class CrossMarketScoreResult:
    config_version: str
    evaluated_at: datetime
    available: bool
    components: tuple[ComponentScore, ...]
    provider_physical_calls: int = 0

    def component(self, name: ComponentName) -> ComponentScore:
        return next(item for item in self.components if item.name is name)

    def canonical_bytes(self) -> bytes:
        payload = {
            "available": self.available,
            "components": [item.to_canonical() for item in self.components],
            "configVersion": self.config_version,
            "evaluatedAt": _instant(self.evaluated_at),
            "providerPhysicalCalls": self.provider_physical_calls,
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


class CrossMarketScorer:
    """252-session empirical percentile와 equal-weight median만 수행하는 순수 kernel이다."""

    _MINIMUM_COVERAGE: Final[dict[ComponentName, int]] = {
        ComponentName.SEMICONDUCTOR: 3,
        ComponentName.BROAD_MARKET: 2,
        ComponentName.FX: 1,
        ComponentName.DOMESTIC_AMPLIFICATION: 3,
    }

    def __init__(self, config_version: str) -> None:
        if not config_version or len(config_version) > 128:
            raise ValueError("cross-market scorer config version is invalid")
        self._config_version = config_version

    def score(
        self,
        series: Sequence[ScorerSeries],
        *,
        evaluated_at: datetime,
    ) -> CrossMarketScoreResult:
        if evaluated_at.tzinfo is None:
            raise ValueError("cross-market evaluated_at must be timezone aware")
        identities = [(item.component, item.instrument) for item in series]
        if len(set(identities)) != len(identities):
            raise ValueError("cross-market scorer series identities must be unique")

        grouped: dict[ComponentName, list[tuple[str, Decimal]]] = {
            name: [] for name in ComponentName
        }
        for item in series:
            percentile = _series_percentile(item, evaluated_at)
            if percentile is not None:
                grouped[item.component].append((item.instrument, percentile))

        components = tuple(self._component(name, grouped[name]) for name in ComponentName)
        return CrossMarketScoreResult(
            config_version=self._config_version,
            evaluated_at=evaluated_at,
            available=all(item.available for item in components),
            components=components,
        )

    def _component(
        self,
        name: ComponentName,
        percentiles: list[tuple[str, Decimal]],
    ) -> ComponentScore:
        ordered = sorted(percentiles, key=lambda item: item[0].encode("utf-8"))
        if len(ordered) < self._MINIMUM_COVERAGE[name]:
            return ComponentScore(
                name=name,
                available=False,
                score=None,
                source_instruments=tuple(item[0] for item in ordered),
                history_session_count=0,
                reason="INSUFFICIENT_COVERAGE",
            )
        score = _median(tuple(item[1] for item in ordered)).quantize(
            _SCORE_QUANTUM,
            rounding=ROUND_HALF_EVEN,
        )
        return ComponentScore(
            name=name,
            available=True,
            score=score,
            source_instruments=tuple(item[0] for item in ordered),
            history_session_count=_HISTORY_SESSIONS,
            reason=None,
        )


def _series_percentile(
    item: ScorerSeries,
    evaluated_at: datetime,
) -> Decimal | None:
    current = item.current_value
    if (
        not item.current_complete
        or current is None
        or not current.is_finite()
        or item.current_available_at.tzinfo is None
        or item.current_available_at > evaluated_at
        or len(item.completed_history) != _HISTORY_SESSIONS
        or any(not value.is_finite() for value in item.completed_history)
    ):
        return None
    transform = Decimal(1) if item.adverse_direction is AdverseDirection.HIGH else Decimal(-1)
    adverse_current = current * transform
    adverse_history = tuple(value * transform for value in item.completed_history)
    lower = sum(value < adverse_current for value in adverse_history)
    equal = sum(value == adverse_current for value in adverse_history)
    return (
        (Decimal(lower) + Decimal(equal) / Decimal(2)) * Decimal(100) / Decimal(_HISTORY_SESSIONS)
    )


def _median(values: tuple[Decimal, ...]) -> Decimal:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal(2)


def _instant(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")
