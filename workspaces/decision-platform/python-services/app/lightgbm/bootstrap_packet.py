"""S5.6 one-shot acquisition packet authoring; provider I/O는 수행하지 않는다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
import hashlib
from typing import Any, Mapping, cast

from exchange_calendars.errors import DateOutOfBounds, RequestedSessionOutOfBounds
import pandas as pd

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
    KST,
    S5_ADHOC_CLOSED_SESSIONS,
    S5_CALENDAR_CORRECTION_SET_SHA256,
    S5_CALENDAR_POLICY_VERSION,
    base_calendar,
    calendar_for_corrections,
    correction_set_sha256,
    corrections_for_sha256,
    build_pit_session_window_for,
    corrected_calendar,
    derive_monthly_universe_schedule_for,
    latest_completed_session,
    previous_xkrx_session,
)
from app.lightgbm.production_policy import (
    APPROVED_HORIZON_UNION_SIZE,
    SUPERSEDED_HORIZON_UNION_SIZES,
    ECOS_OPERATIONS,
    KIS_OPERATION,
    KRX_OPERATIONS,
    BootstrapBudget,
    author_bootstrap_budget,
    author_recovery_bootstrap_budget,
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
    packet_version: str
    calendar_policy_version: str | None
    calendar_correction_set_sha256: str | None
    lineage_mode: str
    recovery_binding_sha256: str | None


def author_bootstrap_packet(*, cutoff: datetime) -> BootstrapPacket:
    """최신 calendar window와 51 monthly schedules가 cap 안인지 provider 전 검증한다."""

    return _author_bootstrap_packet(
        cutoff=cutoff,
        calendar=corrected_calendar(),
        packet_version="s5-production-bootstrap-packet-v2",
        lineage_mode="FRESH",
    )


def author_recovery_bootstrap_packet(
    *,
    cutoff: datetime,
    recovery_binding_sha256: str,
    superseded_allowance: int = 0,
    kis_superseded_allowance: int = 0,
    kis_token_superseded_allowance: int = 0,
) -> BootstrapPacket:
    """Recovery binding을 packet bytes에 넣어 sidecar 삭제 우회를 막는다.

    Superseded allowance도 packet bytes에 봉인해 실행 권위에서 recovery receipt와 journal과
    3자 교차검증되게 한다.
    """

    _require_sha256(recovery_binding_sha256, "recovery binding")
    return _author_bootstrap_packet(
        cutoff=cutoff,
        calendar=corrected_calendar(),
        packet_version="s5-production-bootstrap-packet-v2",
        lineage_mode="CALENDAR_RECOVERY",
        recovery_binding_sha256=recovery_binding_sha256,
        superseded_allowance=superseded_allowance,
        kis_superseded_allowance=kis_superseded_allowance,
        kis_token_superseded_allowance=kis_token_superseded_allowance,
        corrections=S5_ADHOC_CLOSED_SESSIONS,
    )


def _author_bootstrap_packet(
    *,
    cutoff: datetime,
    calendar: Any,
    packet_version: str,
    lineage_mode: str = "HISTORICAL_V1",
    recovery_binding_sha256: str | None = None,
    superseded_allowance: int = 0,
    kis_superseded_allowance: int = 0,
    kis_token_superseded_allowance: int = 0,
    corrections: tuple[date, ...] | None = None,
    union_size: int | None = None,
) -> BootstrapPacket:
    """현재 correction 정책과 historical v1을 같은 closed authoring path로 재생성한다."""

    if packet_version not in {
        "s5-production-bootstrap-packet-v1",
        "s5-production-bootstrap-packet-v2",
    }:
        raise LightGbmContractError("bootstrap packet version is not approved")
    if packet_version == "s5-production-bootstrap-packet-v1":
        if lineage_mode != "HISTORICAL_V1" or recovery_binding_sha256 is not None:
            raise LightGbmContractError("historical bootstrap lineage is invalid")
    elif lineage_mode == "FRESH":
        if recovery_binding_sha256 is not None:
            raise LightGbmContractError("fresh bootstrap cannot bind recovery evidence")
    elif lineage_mode == "CALENDAR_RECOVERY":
        if recovery_binding_sha256 is None:
            raise LightGbmContractError("calendar recovery binding is required")
        _require_sha256(recovery_binding_sha256, "recovery binding")
    else:
        raise LightGbmContractError("bootstrap lineage mode is not approved")
    if (
        superseded_allowance
        or kis_superseded_allowance
        or kis_token_superseded_allowance
    ) and lineage_mode != "CALENDAR_RECOVERY":
        raise LightGbmContractError(
            "superseded allowance is limited to calendar recovery lineage"
        )
    if cutoff.tzinfo is None:
        raise LightGbmContractError("bootstrap cutoff must be timezone aware")
    # v2 packet은 author된 correction 세대를 bytes에 선언한다. 현재 세대가 기본이며, 이미 소비한
    # packet을 read-only로 재생성할 때만 그 packet이 선언한 이전 세대를 넘긴다.
    generation = S5_ADHOC_CLOSED_SESSIONS if corrections is None else corrections
    generation_sha256 = correction_set_sha256(generation)
    approved_union = APPROVED_HORIZON_UNION_SIZE if union_size is None else union_size
    window = build_pit_session_window_for(cutoff, calendar=calendar)
    if _label_as_of(window.raw_sessions[-1], calendar=calendar) > cutoff:
        raise LightGbmContractError(
            "bootstrap cutoff precedes the latest label maturity clock"
        )
    months = _months_between(window.eligible_sessions[0], window.raw_sessions[-1])
    schedules = tuple(
        derive_monthly_universe_schedule_for(
            month,
            dataset_cutoff=cutoff,
            calendar=calendar,
        )
        for month in months
    )
    if lineage_mode == "CALENDAR_RECOVERY":
        budget = author_recovery_bootstrap_budget(
            monthly_schedule_count=len(schedules),
            union_size=approved_union,
            raw_session_count=len(window.raw_sessions),
            superseded_allowance=superseded_allowance,
            kis_superseded_allowance=kis_superseded_allowance,
            kis_token_superseded_allowance=kis_token_superseded_allowance,
        )
    else:
        budget = author_bootstrap_budget(
            monthly_schedule_count=len(schedules),
            union_size=approved_union,
            raw_session_count=len(window.raw_sessions),
        )
    payload = {
        "packetVersion": packet_version,
        **(
            {
                "calendarPolicyVersion": S5_CALENDAR_POLICY_VERSION,
                "calendarCorrectionSetSha256": generation_sha256,
                "lineageMode": lineage_mode,
                **(
                    {"recoveryBindingSha256": recovery_binding_sha256}
                    if recovery_binding_sha256 is not None
                    else {}
                ),
            }
            if packet_version == "s5-production-bootstrap-packet-v2"
            else {}
        ),
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
            # v1 packet bytes는 recovery 검증 전용으로 동결돼 있어 v2에서만 allowance를 봉인한다.
            **(
                {"krxSupersededAllowance": budget.krx_superseded_allowance}
                if packet_version == "s5-production-bootstrap-packet-v2"
                else {}
            ),
            # 이미 봉인된 packet의 bytes를 보존하려면 KIS allowance는 0이 아닐 때만 나타나야 한다.
            **(
                {"kisSupersededAllowance": budget.kis_superseded_allowance}
                if budget.kis_superseded_allowance
                else {}
            ),
            **(
                {
                    "kisTokenSupersededAllowance": (
                        budget.kis_token_superseded_allowance
                    )
                }
                if budget.kis_token_superseded_allowance
                else {}
            ),
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
        packet_version=packet_version,
        calendar_policy_version=(
            S5_CALENDAR_POLICY_VERSION
            if packet_version == "s5-production-bootstrap-packet-v2"
            else None
        ),
        calendar_correction_set_sha256=(
            generation_sha256
            if packet_version == "s5-production-bootstrap-packet-v2"
            else None
        ),
        lineage_mode=lineage_mode,
        recovery_binding_sha256=recovery_binding_sha256,
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


def validate_bootstrap_packet(
    content: bytes,
    *,
    expected_sha256: str,
    allow_historical_v1: bool = False,
    allow_superseded_corrections: bool = False,
) -> BootstrapPacket:
    """외부 승인 SHA와 canonical packet bytes를 calendar에서 재생성해 전수 검증한다.

    v1은 calendar recovery 전용으로만 열며 production executor가 과거 달력을 재사용하지 못하게 한다.
    """

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
    packet_version = cast(dict[str, object], value).get("packetVersion")
    if packet_version == "s5-production-bootstrap-packet-v2":
        lineage_mode = cast(dict[str, object], value).get("lineageMode")
        recovery_binding = cast(dict[str, object], value).get("recoveryBindingSha256")
        corrections = _parse_correction_generation(
            value, allow_superseded=allow_superseded_corrections
        )
        if lineage_mode == "FRESH" and recovery_binding is None:
            kwargs: dict[str, object] = {"lineage_mode": "FRESH"}
        elif lineage_mode == "CALENDAR_RECOVERY" and isinstance(
            recovery_binding, str
        ):
            kwargs = {
                "lineage_mode": "CALENDAR_RECOVERY",
                "recovery_binding_sha256": recovery_binding,
                "superseded_allowance": _parse_superseded_allowance(value),
                "kis_superseded_allowance": _parse_optional_allowance(
                    value, "kisSupersededAllowance"
                ),
                "kis_token_superseded_allowance": _parse_optional_allowance(
                    value, "kisTokenSupersededAllowance"
                ),
            }
        else:
            raise LightGbmContractError("bootstrap recovery lineage is invalid")
        # 승인 차원이 바뀌어도 이미 봉인된 packet은 자기 정책으로 재생성돼야 한다.
        unions: tuple[int, ...] = (APPROVED_HORIZON_UNION_SIZE,)
        if allow_superseded_corrections:
            unions = (APPROVED_HORIZON_UNION_SIZE, *SUPERSEDED_HORIZON_UNION_SIZES)
        regenerated = None
        for candidate_union in unions:
            attempt = _author_bootstrap_packet(
                cutoff=cutoff,
                calendar=calendar_for_corrections(corrections),
                packet_version="s5-production-bootstrap-packet-v2",
                corrections=corrections,
                union_size=candidate_union,
                **kwargs,  # type: ignore[arg-type]
            )
            if attempt.content == content:
                regenerated = attempt
                break
        if regenerated is None:
            raise LightGbmContractError(
                "bootstrap packet does not match current calendar policy"
            )
    elif packet_version == "s5-production-bootstrap-packet-v1" and allow_historical_v1:
        # 이미 소비한 packet/run을 안전하게 supersede할 때만 과거 base-calendar bytes를 검증한다.
        regenerated = _author_bootstrap_packet(
            cutoff=cutoff,
            calendar=base_calendar(),
            packet_version="s5-production-bootstrap-packet-v1",
        )
    else:
        raise LightGbmContractError("bootstrap packet version is not approved")
    if regenerated.content != content or regenerated.sha256 != expected_sha256:
        raise LightGbmContractError("bootstrap packet does not match current calendar policy")
    return regenerated


def _parse_correction_generation(
    value: Mapping[str, object], *, allow_superseded: bool
) -> tuple[date, ...]:
    """packet이 선언한 correction 세대만 인정한다.

    production 실행 경로는 현재 세대만 받는다. 이미 소비한 packet을 recovery가 read-only로
    검증할 때만 승인된 이전 세대를 연다.
    """

    digest = value.get("calendarCorrectionSetSha256")
    if not isinstance(digest, str):
        raise LightGbmContractError("bootstrap packet correction set is invalid")
    if digest == S5_CALENDAR_CORRECTION_SET_SHA256:
        return S5_ADHOC_CLOSED_SESSIONS
    if not allow_superseded:
        raise LightGbmContractError("bootstrap packet does not match current calendar policy")
    return corrections_for_sha256(digest)


def _parse_optional_allowance(value: Mapping[str, object], field: str) -> int:
    """0이 아닐 때만 봉인되는 allowance를 읽는다. 부재는 0을 뜻한다."""

    limits = value.get("limits")
    if not isinstance(limits, dict):
        raise LightGbmContractError("bootstrap packet limits are invalid")
    if field not in limits:
        return 0
    allowance = limits[field]
    if not isinstance(allowance, int) or isinstance(allowance, bool) or allowance <= 0:
        raise LightGbmContractError("bootstrap packet superseded allowance is invalid")
    return allowance


def _parse_superseded_allowance(value: Mapping[str, object]) -> int:
    """Packet limits에 봉인된 allowance만 읽는다. 상한 자체는 budget authoring이 강제한다."""

    limits = value.get("limits")
    if not isinstance(limits, dict):
        raise LightGbmContractError("bootstrap packet limits are invalid")
    allowance = limits.get("krxSupersededAllowance")
    if not isinstance(allowance, int) or isinstance(allowance, bool) or allowance < 0:
        raise LightGbmContractError("bootstrap packet superseded allowance is invalid")
    return allowance


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


def _label_as_of(label_end_session: date, *, calendar: Any) -> datetime:
    """Legacy packet의 maturity도 그 packet이 사용한 calendar로 재현한다."""

    try:
        session = calendar.date_to_session(pd.Timestamp(label_end_session), direction="none")
        target = calendar.next_session(session)
    except Exception:
        raise LightGbmContractError("label end must be an XKRX session") from None

    return datetime.combine(target.date(), time(8, 10), tzinfo=KST)


def _require_sha256(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise LightGbmContractError(f"bootstrap {name} SHA-256 is invalid")
