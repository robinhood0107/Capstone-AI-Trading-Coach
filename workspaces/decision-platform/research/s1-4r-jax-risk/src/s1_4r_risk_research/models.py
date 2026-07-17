"""Frozen result and provenance models for the isolated S1.4R contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class EffectiveTrialProvenance:
    """DSR effective trial 수의 외부 원천과 sampling-frequency 계약을 보존한다."""

    schema_version: Literal["s1.4r-effective-trials-v1"]
    method: Literal[
        "pre_registered_independent",
        "externally_estimated_effective_count",
    ]
    raw_trial_count: int
    effective_trial_count: int
    sampling_frequency: str
    trial_registry_sha256: str
    variance_ddof: Literal[1]


@dataclass(frozen=True, slots=True)
class LikelihoodRatioTestResult:
    """LR statistic과 asymptotic p-value의 Python scalar 계약이다."""

    statistic: float
    p_value: float
    reject: bool
    observations: int
    exceptions: int
    degrees_of_freedom: int
    significance: float


@dataclass(frozen=True, slots=True)
class TransitionCounts:
    """연속 exception indicator에서 계산한 2-state transition counts다."""

    n00: int
    n01: int
    n10: int
    n11: int


@dataclass(frozen=True, slots=True)
class IndependenceTestResult(LikelihoodRatioTestResult):
    """Christoffersen independence 결과와 원 transition counts를 함께 보존한다."""

    transitions: TransitionCounts


@dataclass(frozen=True, slots=True)
class ConditionalCoverageTestResult(LikelihoodRatioTestResult):
    """First-observation-conditioned CC 결과와 두 LR component를 노출한다."""

    transitions: TransitionCounts
    conditioned_observations: int
    conditioned_exceptions: int
    unconditional_component_statistic: float
    independence_component_statistic: float
