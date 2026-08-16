"""S5.6 one-shot acquisition packet authoring; provider I/O는 수행하지 않는다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import hashlib
from typing import cast

from exchange_calendars.errors import DateOutOfBounds, RequestedSessionOutOfBounds

from app.data._shared.bounded_json import (
    BoundedJsonError,
    BoundedJsonLimits,
    parse_bounded_json_bytes,
)
from app.data._shared.canonical_json import canonical_json_bytes
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.pit_calendar import (
    MonthlyUniverseSchedule,
    PitSessionWindow,
    build_pit_session_window,
    derive_monthly_universe_schedule,
    latest_completed_session,
    previous_xkrx_session,
)
from app.lightgbm.production_policy import (
    ECOS_OPERATIONS,
    KIS_OPERATION,
    KRX_OPERATIONS,
    BootstrapBudget,
    author_bootstrap_budget,
)
from app.lightgbm.temporal import label_as_of


@dataclass(frozen=True, slots=True)
class BootstrapPacket:
    """Source data·credential 없이 exact sessions/operations/caps만 담은 approval packet."""

    content: bytes
    sha256: str
    window: PitSessionWindow
    schedules: tuple[MonthlyUniverseSchedule, ...]
    budget: BootstrapBudget


def author_bootstrap_packet(*, cutoff: datetime) -> BootstrapPacket:
    """최신 calendar window와 51 monthly schedules가 cap 안인지 provider 전 검증한다."""

    if cutoff.tzinfo is None:
        raise LightGbmContractError("bootstrap cutoff must be timezone aware")
    window = build_pit_session_window(cutoff)
    if label_as_of(window.raw_sessions[-1]) > cutoff:
        raise LightGbmContractError(
            "bootstrap cutoff precedes the latest label maturity clock"
        )
    months = _months_between(window.eligible_sessions[0], window.raw_sessions[-1])
    schedules = tuple(
        derive_monthly_universe_schedule(month, dataset_cutoff=cutoff) for month in months
    )
    budget = author_bootstrap_budget(
        monthly_schedule_count=len(schedules),
        union_size=180,
        raw_session_count=len(window.raw_sessions),
    )
    payload = {
        "packetVersion": "s5-production-bootstrap-packet-v1",
        "cutoff": _canonical_utc(cutoff),
        "latestCompletedSession": window.latest_completed.isoformat(),
        "rawSessionStart": window.raw_sessions[0].isoformat(),
        "rawSessionEnd": window.raw_sessions[-1].isoformat(),
        "rawSessionCount": len(window.raw_sessions),
        "eligibleSessionStart": window.eligible_sessions[0].isoformat(),
        "eligibleSessionEnd": window.eligible_sessions[-1].isoformat(),
        "eligibleSessionCount": len(window.eligible_sessions),
        "monthlyScheduleCount": len(schedules),
        "monthlySchedules": [
            {
                "effectiveMonth": value.effective_month,
                "firstEffectiveSession": value.first_effective_session.isoformat(),
                "selectionSession": value.selection_session.isoformat(),
                "evidenceCutoff": value.evidence_cutoff.astimezone(UTC)
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z"),
                "trailingSessions": [day.isoformat() for day in value.trailing_sessions],
            }
            for value in schedules
        ],
        "operations": {
            "KRX": list(KRX_OPERATIONS),
            "KIS": [KIS_OPERATION],
            "ECOS": list(ECOS_OPERATIONS),
        },
        "limits": {
            "krxMaxGet": budget.krx_get,
            "kisMaxGet": budget.kis_get,
            "kisTokenMax": budget.kis_token,
            "ecosMaxGet": budget.ecos_get,
            "totalMaxPhysicalCalls": budget.total,
            "retry": 0,
            "costMax": 0,
            "accountBalanceOrderCalls": 0,
        },
        "historicalMode": "HISTORICAL_REPLAY_RECONSTRUCTED",
        "strictProviderPITClaim": False,
    }
    content = canonical_json_bytes(payload)
    return BootstrapPacket(
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        window=window,
        schedules=schedules,
        budget=budget,
    )


def latest_publishable_bootstrap_cutoff(*, cutoff: datetime) -> datetime:
    """현재 시각에서 label이 성숙한 최신 XKRX session의 bootstrap cutoff를 반환한다.

    초기 bootstrap은 provider 호출 전 packet 자체가 label maturity를 증명해야 한다. 휴일 뒤에는
    최신 완료 session이 아직 미성숙할 수 있으므로, 생산 가능한 가장 최근 session의 asOf로 낮춘다.
    """

    if cutoff.tzinfo is None:
        raise LightGbmContractError("bootstrap cutoff must be timezone aware")
    try:
        candidate = latest_completed_session(cutoff)
        while label_as_of(candidate) > cutoff:
            candidate = previous_xkrx_session(candidate)
    except (DateOutOfBounds, RequestedSessionOutOfBounds) as error:
        raise LightGbmContractError(
            "bootstrap cutoff is outside pinned XKRX calendar bounds"
        ) from error
    return label_as_of(candidate)


def validate_bootstrap_packet(content: bytes, *, expected_sha256: str) -> BootstrapPacket:
    """외부 승인 SHA와 canonical packet bytes를 calendar에서 재생성해 전수 검증한다."""

    if (
        len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
        or hashlib.sha256(content).hexdigest() != expected_sha256
    ):
        raise LightGbmContractError("bootstrap packet trust anchor mismatch")
    try:
        value = parse_bounded_json_bytes(
            content,
            limits=BoundedJsonLimits(
                max_bytes=1 * 1024 * 1024,
                max_depth=6,
                max_list_items=2_000,
                max_object_keys=32,
                max_text_codepoints=8_192,
                max_text_bytes=32_768,
                max_number_characters=32,
            ),
        )
    except BoundedJsonError as error:
        raise LightGbmContractError("bootstrap packet JSON is invalid") from error
    if not isinstance(value, dict) or canonical_json_bytes(value) != content:
        raise LightGbmContractError("bootstrap packet must be canonical closed JSON")
    cutoff_value = cast(dict[str, object], value).get("cutoff")
    if not isinstance(cutoff_value, str) or not cutoff_value.endswith("Z"):
        raise LightGbmContractError("bootstrap packet cutoff is invalid")
    try:
        cutoff = datetime.fromisoformat(cutoff_value[:-1] + "+00:00")
    except ValueError:
        raise LightGbmContractError("bootstrap packet cutoff is invalid") from None
    regenerated = author_bootstrap_packet(cutoff=cutoff)
    if regenerated.content != content or regenerated.sha256 != expected_sha256:
        raise LightGbmContractError("bootstrap packet does not match current calendar policy")
    return regenerated


def _months_between(first: date, last: date) -> tuple[str, ...]:
    output: list[str] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        output.append(f"{year:04d}-{month:02d}")
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return tuple(output)


def _canonical_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
