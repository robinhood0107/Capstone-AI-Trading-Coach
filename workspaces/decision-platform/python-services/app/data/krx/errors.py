from __future__ import annotations


class KrxError(RuntimeError):
    """provider 원문이나 인증정보를 경계 밖으로 전달하지 않는 KRX 공통 오류다."""


class KrxParseError(KrxError):
    """공식 응답 계약을 벗어난 KRX payload를 원문 비노출 오류로 거부한다."""

    code = "invalid_response"
    retryable = False

    def __init__(self) -> None:
        super().__init__("krx_parse_error:invalid_response")
