from __future__ import annotations


class CalendarError(RuntimeError):
    """S1.6 내부 경계가 stable code로 분류하는 최상위 오류다."""


class RegistryValidationError(CalendarError):
    """source registry가 strict schema나 activation 안전 규칙을 위반했다."""


class AdapterValidationError(CalendarError):
    """provider fixture가 bounded parser 또는 structured mapping 계약을 위반했다."""


class NetworkActivationError(CalendarError):
    """검증되지 않은 origin 또는 승인되지 않은 online source가 호출되기 전에 발생한다."""


class PrivacyProjectionError(CalendarError):
    """canonical projection에 PII, secret, query 또는 raw provider 자료가 남았다."""


class QuotaReservationDenied(CalendarError):
    """OpenDART charged-attempt reservation이 budget 또는 exhausted 상태로 거부됐다."""


class CollectorAlreadyRunning(CalendarError):
    """PostgreSQL session advisory lock을 다른 collector가 소유한다."""


class RunLimitExceeded(CalendarError):
    """실행별 physical attempt 또는 DB reservation gate가 fail-closed했다."""


class PriorityDeferred(CalendarError):
    """70%/90% project policy가 낮은 priority operation을 HTTP 전에 이월했다."""


class RetryableProviderError(CalendarError):
    """안전한 GET에서만 bounded retry할 수 있는 transient provider 오류다."""

    def __init__(self, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__("transient provider failure")


class NonRetryableProviderError(CalendarError):
    """인증, 인자, 429, schema drift처럼 자동 재시도하면 안 되는 오류다."""

    def __init__(self, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__("non-retryable provider failure")


class ProviderQuotaExhausted(NonRetryableProviderError):
    """OpenDART HTTP 200 body status=020을 당일 전체 중단 신호로 표현한다."""

    def __init__(self) -> None:
        super().__init__(None)


class StateTransitionError(CalendarError):
    """공시 상태 전이 실패를 provider 값 없이 stable code로만 노출한다."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"disclosure state transition failed: {code}")
