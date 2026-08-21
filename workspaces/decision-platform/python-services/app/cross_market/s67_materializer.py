from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final, Mapping
from uuid import UUID, uuid5


_NAMESPACE: Final[UUID] = UUID("ef8f03d1-c529-4f96-a957-c31834534de5")
_SHA256: Final = frozenset("0123456789abcdef")
_THRESHOLDS: Final = frozenset((Decimal("95"), Decimal("97.5"), Decimal("99")))


class EvidenceMode(StrEnum):
    SYNTHETIC_FIXTURE = "SYNTHETIC_FIXTURE"
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    PROSPECTIVE_SHADOW = "PROSPECTIVE_SHADOW"
    MANUAL_EOD = "MANUAL_EOD"


class StorageMode(StrEnum):
    ARTIFACT_ONLY = "ARTIFACT_ONLY"
    STORED_SNAPSHOT = "STORED_SNAPSHOT"


class RuntimeMode(StrEnum):
    OFF = "OFF"
    SHADOW = "SHADOW"
    WARN_ONLY = "WARN_ONLY"
    ENFORCED = "ENFORCED"


class ExposureClassification(StrEnum):
    NEW_BUY = "NEW_BUY"
    INCREASE_BUY = "INCREASE_BUY"
    SELL = "SELL"
    REDUCE = "REDUCE"
    LIQUIDATION = "LIQUIDATION"
    EXISTING_POSITION = "EXISTING_POSITION"
    UNCLASSIFIED = "UNCLASSIFIED"


@dataclass(frozen=True, slots=True)
class ThresholdFreeze:
    selected_percentile: Decimal
    artifact_hash: str
    config_hash: str

    def __post_init__(self) -> None:
        if self.selected_percentile not in _THRESHOLDS:
            raise ValueError("threshold must be one of 95, 97.5, or 99")
        _require_hash(self.artifact_hash, "threshold artifact")
        _require_hash(self.config_hash, "config")


@dataclass(frozen=True, slots=True)
class CrossMarketRiskSnapshotV2:
    payload: Mapping[str, object]
    explanation: Mapping[str, object]

    @property
    def semantic_input_hash(self) -> str:
        return str(self.payload["semanticInputHash"])

    @property
    def artifact_hash(self) -> str:
        return str(self.payload["artifactHash"])

    def canonical_bytes(self) -> bytes:
        return _canonical(self.payload)


class CrossMarketRiskMaterializer:
    """S6.6 freeze와 stored score만 v2 snapshot으로 묶는 I/O-free 경계다."""

    def materialize(
        self,
        *,
        symbol: str,
        available_at: datetime,
        stale_at: datetime,
        score: Decimal | None,
        freeze: ThresholdFreeze | None,
        exposure: ExposureClassification,
        exposure_catalog_hash: str,
        evidence_mode: EvidenceMode,
        storage_mode: StorageMode,
        runtime_mode: RuntimeMode,
        explanation: Mapping[str, object] | None = None,
    ) -> CrossMarketRiskSnapshotV2:
        if runtime_mode is RuntimeMode.ENFORCED:
            raise ValueError("MODE_NOT_APPROVED")
        if not symbol or len(symbol) > 32 or not all(c.isdigit() or c.isupper() or c in "./-" for c in symbol):
            raise ValueError("symbol is invalid")
        if available_at.tzinfo is None or stale_at.tzinfo is None or stale_at < available_at:
            raise ValueError("snapshot chronology is invalid")
        _require_hash(exposure_catalog_hash, "exposure catalog")
        if score is not None and (not score.is_finite() or score < 0 or score > 100):
            raise ValueError("score is invalid")

        available = score is not None and freeze is not None
        threshold = freeze.selected_percentile if freeze is not None else None
        threshold_hash = freeze.artifact_hash if freeze is not None else None
        config_hash = freeze.config_hash if freeze is not None else "0" * 64
        semantic = {
            "availableAt": _instant(available_at),
            "configHash": config_hash,
            "exposure": exposure.value,
            "exposureCatalogHash": exposure_catalog_hash,
            "score": _decimal(score),
            "staleAt": _instant(stale_at),
            "symbol": symbol,
            "thresholdArtifactHash": threshold_hash,
            "thresholdPercentile": _decimal(threshold),
        }
        semantic_hash = _sha256(
            "\n".join(
                (
                    "s6-cross-market-semantic-v2",
                    symbol,
                    _instant(available_at),
                    _instant(stale_at),
                    _decimal(score) or "",
                    _decimal(threshold) or "",
                    threshold_hash or "",
                    config_hash,
                    exposure.value,
                    exposure_catalog_hash,
                )
            ).encode()
        )
        explanation_payload = dict(explanation or {})
        artifact_body = {
            "semantic": semantic,
            "explanation": explanation_payload,
            "explanationAuthority": "NONE",
        }
        artifact_hash = _sha256(_canonical(artifact_body))
        snapshot_id = str(uuid5(_NAMESPACE, semantic_hash))
        payload: dict[str, object] = {
            "artifactHash": artifact_hash,
            "availability": "AVAILABLE" if available else "UNAVAILABLE",
            "availableAt": _instant(available_at),
            "configHash": config_hash,
            "contractId": "cross_market_risk_snapshot.v2",
            "evidenceMode": evidence_mode.value,
            "exposure": exposure.value,
            "exposureAvailableAt": _instant(available_at),
            "exposureCatalogHash": exposure_catalog_hash,
            "providerFanoutAllowed": False,
            "quality": "PASS" if available else "EVIDENCE_GAP",
            "runtimeMode": runtime_mode.value,
            "score": None if score is None else float(score),
            "semanticInputHash": semantic_hash,
            "snapshotId": snapshot_id,
            "staleAt": _instant(stale_at),
            "storageMode": storage_mode.value,
            "symbol": symbol,
            "thresholdArtifactHash": threshold_hash,
            "thresholdPercentile": None if threshold is None else float(threshold),
        }
        return CrossMarketRiskSnapshotV2(payload=payload, explanation=explanation_payload)


def _require_hash(value: str, label: str) -> None:
    if len(value) != 64 or any(character not in _SHA256 for character in value):
        raise ValueError(f"{label} hash is invalid")


def _decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    rendered = format(value.normalize(), "f")
    return "0" if rendered in {"-0", ""} else rendered


def _canonical(payload: Mapping[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _instant(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")
