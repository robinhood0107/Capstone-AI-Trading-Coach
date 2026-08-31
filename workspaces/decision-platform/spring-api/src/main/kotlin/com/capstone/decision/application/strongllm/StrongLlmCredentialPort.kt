package com.capstone.decision.application.strongllm

/** 저장된 provider 키 하나의 봉투다. 평문은 이 안에 없다. */
data class StrongLlmSealedCredential(
    val kekVersion: String,
    val wrapNonce: ByteArray,
    val wrappedDek: ByteArray,
    val wrapTag: ByteArray,
    val keyNonce: ByteArray,
    val keyCiphertext: ByteArray,
    val keyTag: ByteArray,
    val keyLast4: String,
)

class StrongLlmCredentialCorruptedException : RuntimeException("STRONG_LLM_CREDENTIAL_CORRUPTED")

/**
 * provider API 키를 감싸고 푸는 경계다. 구현은 infrastructure에 있고 application은 봉투만 안다.
 */
interface StrongLlmCredentialPort {
    fun seal(
        ownerUserId: String,
        slot: String,
        apiKey: String,
    ): StrongLlmSealedCredential

    /** provider 호출 직전에만 부른다. 반환한 배열은 호출자가 쓰고 나서 지운다. */
    fun open(
        ownerUserId: String,
        slot: String,
        sealed: StrongLlmSealedCredential,
    ): ByteArray
}
