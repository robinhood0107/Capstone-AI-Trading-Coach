from __future__ import annotations

from typing import Literal


NaverErrorCode = Literal[
    "invalid_query",
    "authentication_failed",
    "rate_limited",
    "provider_unavailable",
    "invalid_response",
]


class NaverError(RuntimeError):
    """Provider 원문이나 credential을 경계 밖으로 전달하지 않는 Naver 공통 오류다."""


class NaverParseError(NaverError):
    """형식·상한 계약을 위반한 Naver 응답을 원문 비노출 오류로 거부한다."""

    code: NaverErrorCode = "invalid_response"
    retryable = False

    def __init__(self) -> None:
        super().__init__("naver_parse_error:invalid_response")


class NaverResponseError(NaverError):
    """HTTP/provider 오류를 재시도 판단 가능한 고정 taxonomy로만 표현한다."""

    def __init__(self, code: NaverErrorCode, *, retryable: bool) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(f"naver_response_error:{code}")
