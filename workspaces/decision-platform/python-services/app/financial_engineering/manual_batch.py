from __future__ import annotations

import hashlib
import math
import time
import tracemalloc
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Protocol, TypeVar

import numpy as np

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.market_data.reader import MarketDataOperationalReader
from app.financial_engineering.gbm_monte_carlo import (
    log_return_mean_to_sde_drift,
    run_gbm_monte_carlo,
)
from app.financial_engineering.hmm_regime import HMMRegimeResult, fit_hmm_regime
from app.financial_engineering.mean_reversion import diagnose_mean_reversion

MAX_REPORT_BYTES = 1_048_576
MAX_SNAPSHOT_BYTES = 262_144
CONFIG = {
    "gbmPaths": 10_000,
    "gbmSeed": 71,
    "gbmHorizonSessions": 20,
    "hmmSeeds": [11, 29, 47, 71, 101],
    "hmmTrainFraction": 0.8,
    "meanReversionWindow": 60,
}


@dataclass(frozen=True)
class BatchStep:
    name: str
    status: str
    error_code: str | None
    wall_time_millis: int
    peak_memory_bytes: int

    def payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            "errorCode": self.error_code,
            "wallTimeMillis": self.wall_time_millis,
            "peakMemoryBytes": self.peak_memory_bytes,
        }


@dataclass(frozen=True)
class BatchPublication:
    snapshot: dict[str, object]
    manifest: dict[str, object]
    report_markdown: str


@dataclass(frozen=True)
class BatchResult:
    status: str
    publications: tuple[BatchPublication, ...]
    error_code: str | None
    provider_calls: int = 0


class FinancialEngineeringPublicationPort(Protocol):
    def publish(self, publication: BatchPublication) -> str: ...


T = TypeVar("T")


class ManualFinancialEngineeringBatch:
    """Stored S5.7 reader만 소비하는 manual sequential batch; scheduler/provider port가 없다."""

    def __init__(
        self,
        reader: MarketDataOperationalReader,
        publisher: FinancialEngineeringPublicationPort,
        *,
        n_paths: int = 10_000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not 20 <= n_paths <= 10_000:
            raise ValueError("n_paths_invalid")
        self._reader = reader
        self._publisher = publisher
        self._n_paths = n_paths
        self._clock = clock

    def run(self) -> BatchResult:
        publications: list[BatchPublication] = []
        try:
            for symbol in self._reader.current_symbols():
                publication = self._run_symbol(symbol)
                self._publisher.publish(publication)
                publications.append(publication)
        except Exception as error:
            return BatchResult(
                status="NOT_AVAILABLE" if not publications else "FAILED",
                publications=tuple(publications),
                error_code=type(error).__name__.upper()[:64],
            )
        return BatchResult("COMPLETE", tuple(publications), None)

    def _run_symbol(self, symbol: str) -> BatchPublication:
        steps: list[BatchStep] = []
        observations, collection_step = _measure(
            "STORED_COLLECTION", lambda: self._reader.read_closes(symbol)
        )
        steps.append(collection_step)
        if len(observations) < 60:
            raise ValueError("STORED_HISTORY_INSUFFICIENT")
        closes, feature_step = _measure(
            "FEATURE", lambda: np.asarray([float(item.close) for item in observations], dtype=np.float64)
        )
        steps.append(feature_step)
        inference, inference_step = _measure("INFERENCE", lambda: self._infer(closes))
        steps.append(inference_step)
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("CLOCK_MUST_BE_AWARE")
        session_date = observations[-1].session_date
        source_manifest_hash = _hash_text(
            "\n".join(sorted(item.source_receipt_sha256 for item in observations))
        )
        snapshot, snapshot_step = _measure(
            "SNAPSHOT",
            lambda: _snapshot(
                symbol,
                session_date.isoformat(),
                now,
                source_manifest_hash,
                inference,
                self._n_paths,
            ),
        )
        steps.append(snapshot_step)
        report, report_step = _measure(
            "REPORT", lambda: _report(snapshot, observations[0].session_date.isoformat(), steps)
        )
        steps.append(report_step)
        report_bytes = report.encode()
        if not 1 <= len(report_bytes) <= MAX_REPORT_BYTES:
            raise ValueError("REPORT_SIZE_INVALID")
        manifest = {
            "contractId": "financial_engineering_report_manifest.v1",
            "runId": str(uuid.uuid4()),
            "snapshotArtifactHash": snapshot["artifactHash"],
            "reportArtifactHash": hashlib.sha256(report_bytes).hexdigest(),
            "reportBytes": len(report_bytes),
            "complete": True,
            "steps": [step.payload() for step in steps],
            "createdAt": _timestamp(now),
        }
        return BatchPublication(snapshot, manifest, report)

    def _infer(self, closes: np.ndarray) -> dict[str, object]:
        returns = np.diff(np.log(closes))
        daily_sigma = float(np.std(returns, ddof=1))
        annualized_sigma = daily_sigma * math.sqrt(252.0)
        g = float(np.mean(returns))
        hmm_features = closes.size - 20
        train_rows = max(40, int(hmm_features * 0.8))
        train_rows = min(train_rows, hmm_features)
        hmm = fit_hmm_regime(closes, train_rows=train_rows)
        last_hmm = hmm.observations[-1] if hmm.observations else None
        gbm = run_gbm_monte_carlo(
            s0=float(closes[-1]),
            mu_sde=log_return_mean_to_sde_drift(g, dt=1 / 252, sigma=annualized_sigma),
            sigma=annualized_sigma,
            horizon=20 / 252,
            dt=1 / 252,
            n_paths=self._n_paths,
            seed=71,
        )
        final_gbm = gbm.prefix_metrics[-1]
        mean_reversion = diagnose_mean_reversion(closes)
        return {
            "annualizedVolatility": annualized_sigma,
            "hmmAvailability": hmm.availability,
            "hmmState": None if last_hmm is None else last_hmm.state,
            "hmmRiskOffPosterior": None if last_hmm is None else _risk_off(hmm, last_hmm.posterior),
            "hmmEntropy": None if last_hmm is None else last_hmm.normalized_entropy,
            "hmmSeed": None if hmm.artifact is None else hmm.artifact.selected_seed,
            "gbmQuality": gbm.quality,
            "gbmLossProbability": final_gbm.loss_probability,
            "gbmVaRLoss95Amount": final_gbm.var_loss95_amount.value,
            "gbmTailMeanLoss95Amount": final_gbm.tail_mean_loss95_amount.value,
            "gbmDeterministicStressSeparated": True,
            "ouAvailability": mean_reversion.availability,
            "ouZScore": mean_reversion.z_score,
            "ouHalfLifeSessions": mean_reversion.half_life_sessions,
            "adfPValueReferenceOnly": None if mean_reversion.adf is None else mean_reversion.adf.p_value,
        }


def _measure(name: str, function: Callable[[], T]) -> tuple[T, BatchStep]:
    started = time.perf_counter_ns()
    tracemalloc.start()
    try:
        value = function()
        _, peak = tracemalloc.get_traced_memory()
    except Exception:
        tracemalloc.stop()
        raise
    tracemalloc.stop()
    elapsed = (time.perf_counter_ns() - started) // 1_000_000
    return value, BatchStep(name, "COMPLETE", None, min(elapsed, 1_800_000), min(peak, 2_147_483_648))


def _snapshot(
    symbol: str,
    session_date: str,
    now: datetime,
    source_manifest_hash: str,
    inference: dict[str, object],
    n_paths: int,
) -> dict[str, object]:
    numeric = {key: value for key, value in inference.items() if isinstance(value, (int, float, bool)) or value is None}
    numeric_bytes = canonical_json_bytes(numeric)
    config_hash = hashlib.sha256(canonical_json_bytes({**CONFIG, "gbmPaths": n_paths})).hexdigest()
    base: dict[str, object] = {
        "contractId": "financial_engineering_snapshot.v1",
        "schemaVersion": 1,
        "symbol": symbol,
        "sessionDate": session_date,
        "asOf": _timestamp(now),
        "availableAt": _timestamp(now),
        "sourceManifestHash": source_manifest_hash,
        "configHash": config_hash,
        "numericPayloadHash": hashlib.sha256(numeric_bytes).hexdigest(),
        "availability": "AVAILABLE" if inference["hmmAvailability"] == "AVAILABLE" else "ABSTAIN",
        "quality": "PASS" if inference["gbmQuality"] == "PASS" else "WARN",
        "staleness": "FRESH",
        "numericPayload": numeric,
        "createdAt": _timestamp(now),
    }
    base["artifactHash"] = hashlib.sha256(canonical_json_bytes(base)).hexdigest()
    if len(canonical_json_bytes(base)) > MAX_SNAPSHOT_BYTES:
        raise ValueError("SNAPSHOT_SIZE_INVALID")
    return base


def _report(snapshot: dict[str, object], window_start: str, prior_steps: list[BatchStep]) -> str:
    numeric = snapshot["numericPayload"]
    assert isinstance(numeric, dict)
    return "\n".join(
        [
            "# Financial Engineering Report",
            "",
            "## Data window and source hashes",
            f"- Window: {window_start} to {snapshot['sessionDate']}",
            f"- Source manifest: `{snapshot['sourceManifestHash']}`",
            "- Collection: S5.7 stored reader only; provider calls: 0",
            "",
            "## S1.4 metrics",
            f"- Annualized volatility: {numeric['annualizedVolatility']}",
            "",
            "## HMM regime",
            f"- State/ABSTAIN: {numeric.get('hmmState')}",
            f"- Entropy: {numeric.get('hmmEntropy')}; selected seed: {numeric.get('hmmSeed')}",
            "",
            "## GBM stochastic convergence",
            f"- Loss probability: {numeric['gbmLossProbability']}",
            "- Deterministic stress is a separate non-probabilistic object.",
            "",
            "## OU mean reversion",
            f"- z-score: {numeric.get('ouZScore')}; half-life sessions: {numeric.get('ouHalfLifeSessions')}",
            f"- ADF p-value: {numeric.get('adfPValueReferenceOnly')} (reference only)",
            "",
            "## BSM assumptions and provenance",
            "- Not included: no trusted option contract was supplied to this stored batch.",
            "",
            "## Quality warnings",
            f"- quality={snapshot['quality']}; availability={snapshot['availability']}",
            "",
            "## Runtime and memory",
            *[f"- {step.name}: {step.wall_time_millis} ms, peak {step.peak_memory_bytes} bytes" for step in prior_steps],
            "",
        ]
    )


def write_publications(output_root: Path, publications: tuple[BatchPublication, ...]) -> None:
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output_root.is_symlink():
        raise ValueError("OUTPUT_ROOT_SYMLINK")
    for publication in publications:
        symbol = str(publication.snapshot["symbol"])
        target = output_root / symbol
        target.mkdir(mode=0o700)
        _write_new(target / "financial_engineering_snapshot.v1.json", canonical_json_bytes(publication.snapshot))
        _write_new(target / "financial_engineering_report_manifest.v1.json", canonical_json_bytes(publication.manifest))
        _write_new(target / "financial_engineering_report.md", publication.report_markdown.encode())


def _write_new(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError("OUTPUT_ALREADY_EXISTS")
    with path.open("xb") as stream:
        stream.write(payload)
    path.chmod(0o600)


def _risk_off(hmm: HMMRegimeResult, posterior: tuple[float, float]) -> float:
    artifact = hmm.artifact
    if artifact is None:
        return float("nan")
    index = artifact.label_by_internal_state.index("RISK_OFF")
    return posterior[index]


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
