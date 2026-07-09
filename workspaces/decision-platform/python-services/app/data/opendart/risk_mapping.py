from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Literal, cast

import yaml

MappingStatus = Literal["active", "blocked"]


class RiskMappingValidationError(ValueError):
    pass


@dataclass(frozen=True)
class RiskMappingEntry:
    code: str
    label: str
    status: MappingStatus
    score: float | None = None
    official_endpoint: str | None = None
    official_filter_code: str | None = None
    evidence_level: str | None = None
    calibration_status: str | None = None
    effective_window_days: int | None = None
    condition_field: str | None = None
    condition_values: tuple[str, ...] = ()
    blocked_reason: str | None = None
    source_gap: str | None = None


@dataclass(frozen=True)
class DisclosureRiskMapping:
    version: str
    active_by_code: dict[str, RiskMappingEntry]
    blocked_by_code: dict[str, RiskMappingEntry]


def load_default_risk_mapping() -> DisclosureRiskMapping:
    """패키지에 포함된 S1.2 YAML mapping을 읽어 scorer가 같은 버전을 재사용하게 한다."""
    content = files("app.data.opendart").joinpath("disclosure_risk_mapping.yaml").read_text(encoding="utf-8")
    loaded = yaml.safe_load(content)
    if not isinstance(loaded, dict):
        raise RiskMappingValidationError("OpenDART risk mapping root must be an object")
    return load_risk_mapping_from_dict(loaded)


def load_risk_mapping_from_dict(data: dict[str, Any]) -> DisclosureRiskMapping:
    """active mapping에는 공식 endpoint/filter 근거를 강제하고 blocked 항목은 source gap을 요구한다."""
    version = _required_str(data, "version")
    entries_value = data.get("entries")
    if not isinstance(entries_value, list):
        raise RiskMappingValidationError("OpenDART risk mapping entries must be a list")

    active: dict[str, RiskMappingEntry] = {}
    blocked: dict[str, RiskMappingEntry] = {}
    seen: set[str] = set()
    for raw_entry in entries_value:
        if not isinstance(raw_entry, dict):
            raise RiskMappingValidationError("OpenDART risk mapping entry must be an object")
        entry = _parse_entry(raw_entry)
        if entry.code in seen:
            raise RiskMappingValidationError(f"Duplicate OpenDART risk mapping code: {entry.code}")
        seen.add(entry.code)
        if entry.status == "active":
            _validate_active(entry)
            active[entry.code] = entry
        else:
            _validate_blocked(entry)
            blocked[entry.code] = entry
    return DisclosureRiskMapping(version=version, active_by_code=active, blocked_by_code=blocked)


def _parse_entry(raw: dict[str, Any]) -> RiskMappingEntry:
    status_text = _required_str(raw, "status")
    if status_text not in {"active", "blocked"}:
        raise RiskMappingValidationError(f"Unsupported OpenDART risk mapping status: {status_text}")
    status = cast(MappingStatus, status_text)
    score = raw.get("score")
    condition_values = raw.get("condition_values") or []
    if not isinstance(condition_values, list):
        raise RiskMappingValidationError("condition_values must be a list")
    window_days = raw.get("effective_window_days")
    if window_days is not None and (not isinstance(window_days, int) or window_days <= 0):
        raise RiskMappingValidationError("effective_window_days must be a positive integer")
    return RiskMappingEntry(
        code=_required_str(raw, "code"),
        label=_required_str(raw, "label"),
        status=status,
        score=float(score) if score is not None else None,
        official_endpoint=_optional_str(raw.get("official_endpoint")),
        official_filter_code=_optional_str(raw.get("official_filter_code")),
        evidence_level=_optional_str(raw.get("evidence_level")),
        calibration_status=_optional_str(raw.get("calibration_status")),
        effective_window_days=window_days,
        condition_field=_optional_str(raw.get("condition_field")),
        condition_values=tuple(_optional_str(value) or "" for value in condition_values),
        blocked_reason=_optional_str(raw.get("blocked_reason")),
        source_gap=_optional_str(raw.get("source_gap")),
    )


def _validate_active(entry: RiskMappingEntry) -> None:
    if entry.score is None or not 0.0 <= entry.score <= 1.0:
        raise RiskMappingValidationError(f"Active mapping {entry.code} must define score between 0 and 1")
    if not entry.official_endpoint and not entry.official_filter_code:
        # report_nm 문자열 매칭을 active 근거로 쓰지 못하게 공식 endpoint/filter 증거를 강제한다.
        raise RiskMappingValidationError(
            f"Active mapping {entry.code} must define official_endpoint or official_filter_code"
        )
    if entry.condition_field and not entry.condition_values:
        raise RiskMappingValidationError(f"Conditional mapping {entry.code} must define condition_values")
    if not entry.evidence_level or not entry.calibration_status:
        raise RiskMappingValidationError(f"Active mapping {entry.code} must define evidence_level and calibration_status")
    if entry.effective_window_days is None:
        # 상태 지속형(부도/감사의견 등)이 30일 기본값에 묶여 조용히 사라지는 회귀를 막으려고 명시 유효기간을 강제한다.
        raise RiskMappingValidationError(f"Active mapping {entry.code} must define effective_window_days")
    if entry.calibration_status not in {"policy_v1_unvalidated", "korea_market_calibrated"}:
        raise RiskMappingValidationError(f"Unsupported calibration_status for active mapping {entry.code}")


def _validate_blocked(entry: RiskMappingEntry) -> None:
    if not entry.blocked_reason or not entry.source_gap:
        raise RiskMappingValidationError(f"Blocked mapping {entry.code} must define blocked_reason and source_gap")


def _required_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RiskMappingValidationError(f"OpenDART risk mapping field {key} is required")
    return value.strip()


def _optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value).strip()
