from __future__ import annotations


class ECOSError(RuntimeError):
    """ECOS 경계 밖으로 provider 원문을 전달하지 않는 공통 오류다."""


class ECOSParseError(ECOSError):
    """형식 또는 안전 상한을 위반한 ECOS 응답을 고정 메시지로 거부한다."""


class ECOSApplicationError(ECOSError):
    """HTTP 200 응답의 ECOS application code만 보존하는 안전한 오류다."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        cooldown_seconds: int = 0,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.cooldown_seconds = cooldown_seconds
        super().__init__(f"ecos_application_error:{code}")


class RegistryNotVerifiedError(ECOSError):
    """공식 metadata 검증 전 provisional series의 online 사용을 차단한다."""
