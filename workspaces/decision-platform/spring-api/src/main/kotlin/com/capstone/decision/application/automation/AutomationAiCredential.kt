package com.capstone.decision.application.automation

/**
 * AI 검토 한 번에 쓸 자격증명. OWNER 일 때만 사용자 서비스 계정(Base64 한 줄)을 들고 있고,
 * OPERATOR 는 아무 재료도 들고 있지 않다 - 운영자 서비스 계정은 에이전트가 배포 비밀에서만 읽는다.
 *
 * 재료는 호출이 끝나면 [close] 로 지운다. 문자열로 바꾸지 않고 로그에 찍히지 않게 toString 을 가린다.
 */
class AutomationAiCredential private constructor(
    val source: AutomationAiCredentialSource,
    private val ownerServiceAccountB64: ByteArray?,
) : AutoCloseable {
    /** 전송 직전에만 부른다. 돌려준 배열은 이 객체가 닫힐 때 함께 지워진다. */
    fun ownerServiceAccountB64(): ByteArray? = ownerServiceAccountB64

    override fun close() {
        ownerServiceAccountB64?.fill(0)
    }

    override fun toString(): String = "AutomationAiCredential($source, <redacted>)"

    companion object {
        fun operator(): AutomationAiCredential = AutomationAiCredential(AutomationAiCredentialSource.OPERATOR, null)

        fun owner(serviceAccountB64: ByteArray): AutomationAiCredential {
            require(serviceAccountB64.isNotEmpty())
            return AutomationAiCredential(AutomationAiCredentialSource.OWNER, serviceAccountB64)
        }
    }
}
