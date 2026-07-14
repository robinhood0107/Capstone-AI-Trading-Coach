from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal


NaverProfileName = Literal["legacy", "api-hub"]

_KST = timezone(timedelta(hours=9))
_LEGACY_HARD_STOP = datetime(2027, 6, 30, tzinfo=_KST)


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


def profile_for(profile: str, *, now: datetime | None = None) -> NaverProfile:
    """명시 profile의 현재 lifecycle gate를 검사하고 고정 정책을 반환한다.

    API Hub는 source-controlled 활성화 전까지 거부하고 legacy는 KST hard stop부터 거부한다.
    """
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        raise ValueError("profile_time_invalid")

    if profile == "legacy":
        if checked_at >= _LEGACY_HARD_STOP:
            raise ValueError("legacy_hard_stop")
        return LEGACY_PROFILE
    if profile == "api-hub":
        if not API_HUB_PROFILE.enabled:
            raise ValueError("profile_disabled")
        return API_HUB_PROFILE
    raise ValueError("profile_unknown")
