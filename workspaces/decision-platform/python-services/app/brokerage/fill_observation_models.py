"""S3.3 sanitized fill observation의 immutable application model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class FillObservationRecord:
    """원문 체결번호와 provider payload를 제외한 append-only DB 입력 계약이다."""

    observation_id: str
    order_id: str
    provider_exec_ref_hash: str
    exec_type: str
    fill_quantity: int
    fill_price_krw: int | None
    cumulative_quantity: int
    leaves_quantity: int
    average_fill_price_krw: int | None
    observed_at: datetime
    received_at: datetime
    schema_version: str
    source_version: str
    source_ref: str
    completeness: str
    artifact_hash: str
