from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.data.ecos.errors import ECOSDiagnostic, ECOSError, ECOSParseError
from app.data.ecos.models import StatisticItemMetadata, StatisticTableMetadata
from app.data.ecos.series_registry import CANDIDATE_SERIES, ECOSSeries


class _MetadataClient(Protocol):
    def statistic_table_list(self, *, series: ECOSSeries) -> StatisticTableMetadata: ...

    def statistic_item_list(self, *, series: ECOSSeries) -> StatisticItemMetadata: ...


@dataclass(frozen=True)
class RegistryExpectation:
    """source-controlled activation 검토에 필요한 sanitized metadata 기대값이다."""

    series: ECOSSeries
    table_name: str
    item_name: str
    unit: str

    def __post_init__(self) -> None:
        if not self.table_name or not self.item_name or not self.unit:
            raise ValueError("ECOS registry expectation is incomplete")
        if any(len(value) > 256 or value != value.strip() for value in self.values):
            raise ValueError("ECOS registry expectation is out of bounds")

    @property
    def values(self) -> tuple[str, str, str]:
        return (self.table_name, self.item_name, self.unit)


@dataclass(frozen=True)
class RegistryMetadataEntry:
    """한 candidate의 allowlist된 table/item metadata만 보존한다."""

    series_id: str
    stat_code: str
    item_code: str
    table_name: str
    item_name: str
    cycle: str
    unit: str
    searchable: bool


@dataclass(frozen=True)
class RegistryInspectionResult:
    """자동 activation 없이 operator 검토용 sanitized metadata와 관측 시각만 반환한다."""

    observed_at: datetime
    entries: tuple[RegistryMetadataEntry, ...]
    can_activate: bool = False
    verified_series: tuple[ECOSSeries, ...] = ()


@dataclass(frozen=True)
class RegistryPreflightResult:
    """raw response·URL·credential 없이 activation 비교 결과와 verified copy만 반환한다."""

    can_activate: bool
    verified_series: tuple[ECOSSeries, ...]
    registry_verified_at: datetime | None


def inspect_registry_metadata(
    *,
    client: _MetadataClient,
    series: Sequence[ECOSSeries],
    observed_at: datetime | None = None,
) -> RegistryInspectionResult:
    """명시된 두 candidate마다 TableList→ItemList를 한 번씩 호출해 sanitized 결과를 만든다.

    retry·파일 쓰기·registry activation은 이 경계에 없으며, 호출 성공 시에도 source registry는
    provisional 상태로 유지된다.
    """
    entries = tuple(series)
    _require_candidate_identities(entries)
    timestamp = _require_utc_evidence(observed_at) if observed_at is not None else None
    observations: list[RegistryMetadataEntry] = []
    for index, entry in enumerate(entries):
        table_ordinal = index * 2 + 1
        item_ordinal = table_ordinal + 1
        try:
            table = client.statistic_table_list(series=entry)
        except ECOSError as error:
            error.enrich_diagnostic(
                request_ordinal=table_ordinal,
                service="StatisticTableList",
                series_id=entry.series_id,
            )
            raise
        if table.stat_code != entry.stat_code or table.cycle != entry.cycle:
            field = "stat_code" if table.stat_code != entry.stat_code else "cycle"
            raise _registry_boundary_error(
                failure_stage="registry_identity",
                failure_reason="identity_mismatch",
                request_ordinal=table_ordinal,
                service="StatisticTableList",
                series_id=entry.series_id,
                field=field,
                field_kind="mismatch",
            )
        if not table.searchable:
            # 검색 불가 series는 ItemList 추가 호출 전에 차단해 실패한 승인 budget을 보존한다.
            raise _registry_boundary_error(
                failure_stage="searchability",
                failure_reason="not_searchable",
                request_ordinal=table_ordinal,
                service="StatisticTableList",
                series_id=entry.series_id,
                field="searchable",
                field_kind="mismatch",
            )
        try:
            item = client.statistic_item_list(series=entry)
        except ECOSError as error:
            error.enrich_diagnostic(
                request_ordinal=item_ordinal,
                service="StatisticItemList",
                series_id=entry.series_id,
            )
            raise
        if (
            item.stat_code != entry.stat_code
            or item.item_code != entry.item_code1
            or item.cycle != entry.cycle
        ):
            if item.stat_code != entry.stat_code:
                field = "stat_code"
            elif item.item_code != entry.item_code1:
                field = "item_code"
            else:
                field = "cycle"
            raise _registry_boundary_error(
                failure_stage="registry_identity",
                failure_reason="identity_mismatch",
                request_ordinal=item_ordinal,
                service="StatisticItemList",
                series_id=entry.series_id,
                field=field,
                field_kind="mismatch",
            )
        observations.append(
            RegistryMetadataEntry(
                series_id=entry.series_id,
                stat_code=table.stat_code,
                item_code=item.item_code,
                table_name=table.name,
                item_name=item.name,
                cycle=table.cycle,
                unit=item.unit,
                searchable=table.searchable,
            )
        )
    # 기본 evidence 시각은 네 metadata call이 모두 성공한 뒤에만 확정한다.
    completed_at = timestamp or _require_utc_evidence(_utc_now())
    return RegistryInspectionResult(observed_at=completed_at, entries=tuple(observations))


def run_registry_preflight(
    *,
    client: _MetadataClient,
    expectations: Sequence[RegistryExpectation],
    verified_at: datetime | None = None,
) -> RegistryPreflightResult:
    """4-call inspection을 source 기대값과 비교하되 source file을 자동 수정하지 않는다."""
    expected = tuple(expectations)
    _require_candidate_identities(tuple(entry.series for entry in expected))
    inspection = inspect_registry_metadata(
        client=client,
        series=tuple(entry.series for entry in expected),
        observed_at=verified_at,
    )
    if not all(
        _metadata_matches(expectation, observation=observation)
        for expectation, observation in zip(expected, inspection.entries, strict=True)
    ):
        return RegistryPreflightResult(
            can_activate=False,
            verified_series=(),
            registry_verified_at=None,
        )
    timestamp = inspection.observed_at
    return RegistryPreflightResult(
        can_activate=True,
        verified_series=tuple(
            expectation.series.model_copy(
                update={
                    "verified": True,
                    "name": observation.item_name,
                    "unit": observation.unit,
                    "registry_verified_at": timestamp,
                }
            )
            for expectation, observation in zip(expected, inspection.entries, strict=True)
        ),
        registry_verified_at=timestamp,
    )


def _metadata_matches(
    expectation: RegistryExpectation,
    *,
    observation: RegistryMetadataEntry,
) -> bool:
    series = expectation.series
    return (
        observation.series_id == series.series_id
        and observation.stat_code == series.stat_code
        and observation.item_code == series.item_code1
        and observation.table_name == expectation.table_name
        and observation.item_name == expectation.item_name
        and observation.cycle == series.cycle
        and observation.searchable
        and observation.unit == expectation.unit
    )


def _require_candidate_identities(entries: Sequence[ECOSSeries]) -> None:
    expected = tuple(
        (series.series_id, series.stat_code, series.item_code1, series.cycle)
        for series in CANDIDATE_SERIES
    )
    actual = tuple(
        (series.series_id, series.stat_code, series.item_code1, series.cycle) for series in entries
    )
    if actual != expected:
        raise ValueError("ECOS registry preflight requires exactly two unique series")


def _registry_boundary_error(
    *,
    failure_stage: str,
    failure_reason: str,
    request_ordinal: int,
    service: str,
    series_id: str,
    field: str,
    field_kind: str,
) -> ECOSParseError:
    """client 모델이 source identity와 어긋나면 값 원문 없이 기존 ECOS parse 오류로 수렴한다."""
    return ECOSParseError(
        "invalid ECOS response",
        diagnostic=ECOSDiagnostic(
            failure_stage=failure_stage,
            failure_reason=failure_reason,
            request_ordinal=request_ordinal,
            service=service,
            series_id=series_id,
            field=field,
            field_kind=field_kind,
        ),
    )


def _require_utc_evidence(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("ECOS registry evidence timestamp must be UTC")
    return value


def _utc_now() -> datetime:
    return datetime.now(UTC)
