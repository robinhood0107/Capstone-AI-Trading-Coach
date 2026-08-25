"""S5.4 gain/contribution report와 calibrated Signal internal artifact export."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol

import numpy as np

from app.data._shared.canonical_json import canonical_json_bytes, canonical_json_sha256
from app.lightgbm.drift import highest_precedence_reason
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.metrics import tie_aware_argmax
from app.lightgbm.training import TrainedBooster

MAX_CONTRIBUTION_ROWS = 500
SIGNALS = ("SELL", "HOLD", "BUY")


class ContributionBooster(Protocol):
    """LightGBM built-in gain/pred_contrib boundary의 최소 protocol."""

    def feature_importance(
        self, importance_type: str = "split", iteration: int | None = None
    ) -> np.ndarray: ...

    def feature_name(self) -> list[str]: ...

    def predict(self, data: np.ndarray, **kwargs: object) -> object: ...


@dataclass(frozen=True)
class SignalArtifactIdentity:
    """internal artifact의 content/provenance binding inputs."""

    symbol: str
    session_date: date
    evaluation_id: str
    model_version: str
    model_report_id: str
    dataset_sha256: str
    model_sha256: str
    report_sha256: str
    payload_sha256: str
    provenance_sha256: str
    fixture: bool
    provenance_class: str


@dataclass(frozen=True)
class InternalModelScore:
    """public API와 DB read projection에 노출하지 않는 raw margin/probability."""

    raw_margins: tuple[float, float, float]
    calibrated_probabilities: tuple[float, float, float]


@dataclass(frozen=True)
class ExportedSignalArtifact:
    """closed internal artifact payload와 별도 internal modelScore."""

    payload: dict[str, object]
    model_score: InternalModelScore | None


def gain_importance(model: TrainedBooster) -> dict[str, float]:
    """선택 model의 모든 feature에 대한 gain importance를 빠짐없이 반환한다."""

    names = model.booster.feature_name()
    gains = np.asarray(
        model.booster.feature_importance(importance_type="gain", iteration=model.best_iteration),
        dtype=np.float64,
    )
    if (
        len(names) == 0
        or gains.shape != (len(names),)
        or not np.isfinite(gains).all()
        or (gains < 0).any()
    ):
        raise LightGbmContractError("LightGBM gain importance is invalid")
    return {name: float(value) for name, value in zip(names, gains, strict=True)}


def curated_contribution_report(
    booster: ContributionBooster,
    features: np.ndarray,
    *,
    row_keys: Sequence[tuple[str, date]],
    dataset_hash: str,
    feature_names: Sequence[str],
    best_iteration: int,
) -> bytes:
    """hash 순 최대 500 untouched rows의 built-in contribution을 비식별 report로 만든다."""

    values = np.asarray(features, dtype=np.float64)
    if values.ndim != 2 or len(values) != len(row_keys) or values.shape[1] != len(feature_names):
        raise LightGbmContractError("contribution input shape is invalid")
    ordering = sorted(
        range(len(row_keys)),
        key=lambda index: hashlib.sha256(
            f"{dataset_hash}|{row_keys[index][0]}|{row_keys[index][1].isoformat()}".encode()
        ).digest(),
    )[:MAX_CONTRIBUTION_ROWS]
    selected = values[ordering]
    raw = np.asarray(
        booster.predict(selected, raw_score=True, num_iteration=best_iteration),
        dtype=np.float64,
    )
    contributions = np.asarray(
        booster.predict(selected, pred_contrib=True, num_iteration=best_iteration),
        dtype=np.float64,
    )
    width = len(feature_names) + 1
    if raw.shape != (len(ordering), 3) or contributions.shape != (len(ordering), width * 3):
        raise LightGbmContractError("LightGBM multiclass contribution shape is invalid")
    reshaped = contributions.reshape(len(ordering), 3, width)
    if not np.allclose(reshaped.sum(axis=2), raw, rtol=0.0, atol=1e-6):
        raise LightGbmContractError("LightGBM contribution does not add to raw margin")
    rows: list[dict[str, object]] = []
    for output_index, source_index in enumerate(ordering):
        symbol, session_date = row_keys[source_index]
        row_hash = hashlib.sha256(
            f"s5-contribution-row-v1|{dataset_hash}|{symbol}|{session_date.isoformat()}".encode()
        ).hexdigest()
        rows.append(
            {
                "rowKeyHash": row_hash,
                "classes": [
                    {
                        "classIndex": class_index,
                        "bias": float(reshaped[output_index, class_index, -1]),
                        "contributions": [
                            float(value) for value in reshaped[output_index, class_index, :-1]
                        ],
                        "rawMargin": float(raw[output_index, class_index]),
                    }
                    for class_index in range(3)
                ],
            }
        )
    return canonical_json_bytes(
        {
            "reportVersion": "s5-pred-contrib-v1",
            "reportOnly": True,
            "featureNames": list(feature_names),
            "rowCount": len(rows),
            "rows": rows,
        }
    )


def export_signal_artifact(
    identity: SignalArtifactIdentity,
    *,
    as_of: datetime | None,
    current_completed_session: date,
    calibrated_probabilities: np.ndarray | None,
    raw_margins: np.ndarray | None,
    failure_reasons: Sequence[str] = (),
) -> ExportedSignalArtifact:
    """PASS prediction만 AVAILABLE로 내보내고 failure/drift/stale/missing은 typed ABSTAIN한다."""

    _validate_identity(identity)
    reasons = list(failure_reasons)
    if identity.session_date != current_completed_session:
        reasons.append("STALE_EVIDENCE")
    reason = highest_precedence_reason(reasons)
    common: dict[str, object] = {
        "artifactVersion": "lightgbm-signal-artifact-v1",
        "schemaVersion": "signal-v2-runtime-v1",
        "producer": "LIGHTGBM",
        "sourceWorkspace": "decision-platform",
        "symbol": identity.symbol,
        "sessionDate": identity.session_date.isoformat(),
        "evaluationId": identity.evaluation_id,
        "timeframe": "1d",
        "modelVersion": identity.model_version,
        "modelReportId": identity.model_report_id,
        "fixture": identity.fixture,
        "provenanceClass": identity.provenance_class,
        "datasetSha256": identity.dataset_sha256,
        "modelSha256": identity.model_sha256,
        "reportSha256": identity.report_sha256,
        "payloadSha256": identity.payload_sha256,
        "provenanceSha256": identity.provenance_sha256,
    }
    if reason is not None:
        return ExportedSignalArtifact({**common, "status": "ABSTAIN", "reason": reason}, None)
    if (
        as_of is None
        or as_of.tzinfo is None
        or calibrated_probabilities is None
        or raw_margins is None
    ):
        return ExportedSignalArtifact(
            {**common, "status": "ABSTAIN", "reason": "MISSING_EVIDENCE"}, None
        )
    probabilities = np.asarray(calibrated_probabilities, dtype=np.float64)
    margins = np.asarray(raw_margins, dtype=np.float64)
    if (
        probabilities.shape != (3,)
        or margins.shape != (3,)
        or not np.isfinite(probabilities).all()
        or not np.isfinite(margins).all()
        or (probabilities < 0).any()
        or not np.isclose(probabilities.sum(), 1.0, rtol=0.0, atol=1e-12)
    ):
        return ExportedSignalArtifact(
            {**common, "status": "ABSTAIN", "reason": "UNIDENTIFIABLE_OUTPUT"}, None
        )
    predicted = int(tie_aware_argmax(probabilities.reshape(1, 3))[0])
    payload = {
        **common,
        "status": "AVAILABLE",
        "asOf": as_of.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "signal": SIGNALS[predicted],
        "confidence": float(probabilities[predicted]),
    }
    return ExportedSignalArtifact(
        payload,
        InternalModelScore(
            raw_margins=tuple(float(value) for value in margins),  # type: ignore[arg-type]
            calibrated_probabilities=tuple(float(value) for value in probabilities),  # type: ignore[arg-type]
        ),
    )


def signal_semantic_hash(payload: dict[str, object]) -> str:
    """public-authority field만 canonical hash하며 contribution/cross-market input은 받지 않는다."""

    return canonical_json_sha256(payload)


def report_id(report: dict[str, object]) -> str:
    """semantic report content에서 wall-clock 없는 modelReportId를 만든다."""

    return f"mrp-{canonical_json_sha256(report)[:12]}"


def _validate_identity(identity: SignalArtifactIdentity) -> None:
    digests = (
        identity.dataset_sha256,
        identity.model_sha256,
        identity.report_sha256,
        identity.payload_sha256,
        identity.provenance_sha256,
    )
    if any(
        len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        for value in digests
    ):
        raise LightGbmContractError("Signal artifact digest is invalid")
    if identity.fixture != (identity.provenance_class == "FAKE_CONTRACT"):
        raise LightGbmContractError("fake Signal artifact provenance is invalid")
