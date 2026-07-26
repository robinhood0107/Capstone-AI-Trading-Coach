package com.capstone.decision.application.security

/**
 * 금융 부작용 write 경로가 공유하는 idempotency key 입력 계약이다.
 * 원문은 요청 처리 중에만 사용하며 로그·metric·영속 저장에는 전달하지 않는다.
 */
object IdempotencyKeyPolicy {
    fun isValid(
        value: String?,
        configuredMaxLength: Int = MAX_LENGTH,
    ): Boolean =
        value != null &&
            value.length in MIN_LENGTH..minOf(configuredMaxLength, MAX_LENGTH) &&
            ALLOWED.matches(value)

    const val MIN_LENGTH: Int = 16
    const val MAX_LENGTH: Int = 128
    const val PATTERN: String = "^[A-Za-z0-9._:-]+$"
    private val ALLOWED = Regex(PATTERN)
}
