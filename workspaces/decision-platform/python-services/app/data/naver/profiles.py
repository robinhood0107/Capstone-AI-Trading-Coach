from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal


NaverProfileName = Literal["legacy", "api-hub"]

_KST = timezone(timedelta(hours=9), name="KST")


@dataclass(frozen=True)
class NaverProfile:
    """profile별 고정 origin·News endpoint·인증 header 이름과 활성 상태를 묶는다.

    값은 source-controlled 정책이며 caller나 환경변수가 origin·path·header를 바꿀 수 없다.
    """

    name: NaverProfileName
    provider_profile: str
    origin: str
    path: str
    auth_headers: tuple[str, str]
    enabled: bool


@dataclass(frozen=True)
class NaverMigrationLifecycle:
    """API Hub 전환 목표와 승인 단계를 기록하되 날짜 기반 자동 전환에는 사용하지 않는다."""

    credential_preparation_period: Literal["2026-Q3"]
    validation_period: Literal["2026-Q4"]
    offline_validation_required: bool
    minimal_online_requires_separate_approval: bool
    cutover_control: Literal["operator-controlled"]
    automatic_date_switch: bool
    target_cutover: date
    legacy_rollback_removal: date
    legacy_hard_stop: datetime


NAVER_MIGRATION_LIFECYCLE = NaverMigrationLifecycle(
    credential_preparation_period="2026-Q3",
    validation_period="2026-Q4",
    offline_validation_required=True,
    minimal_online_requires_separate_approval=True,
    cutover_control="operator-controlled",
    automatic_date_switch=False,
    target_cutover=date(2027, 3, 31),
    legacy_rollback_removal=date(2027, 5, 31),
    legacy_hard_stop=datetime(2027, 6, 30, tzinfo=_KST),
)


LEGACY_PROFILE = NaverProfile(
    name="legacy",
    provider_profile="naver-legacy",
    origin="https://openapi.naver.com",
    path="/v1/search/news.json",
    auth_headers=("X-Naver-Client-Id", "X-Naver-Client-Secret"),
    enabled=True,
)

API_HUB_PROFILE = NaverProfile(
    name="api-hub",
    provider_profile="naver-api-hub",
    origin="https://naverapihub.apigw.ntruss.com",
    path="/search/v1/news",
    auth_headers=("X-NCP-APIGW-API-KEY-ID", "X-NCP-APIGW-API-KEY"),
    enabled=False,
)


def require_canonical_profile(profile: NaverProfile) -> NaverProfile:
    """credential transport에는 source-controlled singleton profile만 허용한다."""
    if profile is LEGACY_PROFILE or profile is API_HUB_PROFILE:
        return profile
    raise ValueError("profile_not_canonical")


def profile_for(profile: str, *, now: datetime | None = None) -> NaverProfile:
    """명시 profile의 현재 lifecycle gate를 검사하고 고정 정책을 반환한다.

    API Hub는 source-controlled 활성화 전까지 거부하고 legacy는 KST hard stop부터 거부한다.
    """
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        raise ValueError("profile_time_invalid")

    if profile == "legacy":
        if checked_at >= NAVER_MIGRATION_LIFECYCLE.legacy_hard_stop:
            raise ValueError("legacy_hard_stop")
        return LEGACY_PROFILE
    if profile == "api-hub":
        if not API_HUB_PROFILE.enabled:
            raise ValueError("profile_disabled")
        return API_HUB_PROFILE
    raise ValueError("profile_unknown")
