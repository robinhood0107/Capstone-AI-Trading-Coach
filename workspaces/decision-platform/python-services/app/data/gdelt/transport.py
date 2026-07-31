from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from app.data.gdelt.errors import GdeltAggregateError

GDELT_ORIGIN = "https://api.gdeltproject.org"
GDELT_PATH = "/api/v2/doc/doc"
GDELT_URL = f"{GDELT_ORIGIN}{GDELT_PATH}"
ALLOWED_MODES = ("TIMELINE_TONE", "TIMELINE_VOL_RAW")


@dataclass(frozen=True)
class FixtureResponse:
    """synthetic fixture bytes와 HTTP-equivalent metadata만 전달하는 network-free 응답이다."""

    content: bytes
    content_type: str
    redirected: bool


class FixtureTransport:
    """실제 socket을 만들지 않고 allowlisted mode fixture만 순차 반환한다."""

    physical_attempt_count = 0

    def __init__(self, responses: dict[str, FixtureResponse]) -> None:
        self._responses = dict(responses)
        self.requests: list[str] = []

    def fetch(self, mode: str) -> FixtureResponse:
        """mode별 합성 응답을 반환하며 호출 기록은 physical provider count에 포함되지 않는다."""

        if mode not in ALLOWED_MODES:
            raise GdeltAggregateError("PROVIDER_DISABLED", "mode is not allowlisted")
        self.requests.append(mode)
        try:
            return self._responses[mode]
        except KeyError:
            raise GdeltAggregateError("INCOMPLETE_SOURCE", "fixture mode is missing") from None


def validate_fixture_response(response: FixtureResponse) -> bytes:
    """fixture도 redirect·content-type 계약을 통과시켜 future transport와 parser 경계를 맞춘다."""

    if response.redirected:
        raise GdeltAggregateError("INVALID_RESPONSE", "redirect is forbidden")
    media_type = response.content_type.split(";", maxsplit=1)[0].strip().lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        raise GdeltAggregateError("INVALID_RESPONSE", "content type is not JSON")
    return response.content


def validate_online_target(*, url: str, follow_redirects: bool, trust_env: bool) -> str:
    """future online target의 exact origin/path와 no-redirect/no-proxy 정책만 검증한다.

    이 함수는 DNS lookup이나 HTTP 요청을 수행하지 않는다. 실제 transport activation에는 별도
    승인 packet과 DNS/IP rebinding 검증이 필요하다.
    """

    parts = urlsplit(url)
    safe = (
        url == GDELT_URL
        and parts.scheme == "https"
        and parts.hostname == "api.gdeltproject.org"
        and parts.port is None
        and parts.path == GDELT_PATH
        and not parts.query
        and not parts.fragment
        and parts.username is None
        and parts.password is None
        and not follow_redirects
        and not trust_env
    )
    if not safe:
        raise GdeltAggregateError("PROVIDER_DISABLED", "online target policy rejected")
    return GDELT_URL
