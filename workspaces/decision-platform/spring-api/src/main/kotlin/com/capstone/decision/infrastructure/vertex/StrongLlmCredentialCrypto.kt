package com.capstone.decision.infrastructure.vertex

import com.capstone.decision.application.strongllm.StrongLlmCredentialCorruptedException
import com.capstone.decision.application.strongllm.StrongLlmCredentialPort
import com.capstone.decision.application.strongllm.StrongLlmSealedCredential
import com.capstone.decision.infrastructure.rag.RagHistoryKekProvider
import org.springframework.stereotype.Component
import java.nio.charset.StandardCharsets
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * provider API 키를 RAG 답변 이력과 같은 KEK 봉투로 감싼다.
 *
 * 이력 암호기를 그대로 쓰지 않는 이유는 AAD 때문이다. 그쪽 AAD는 answerId와 생성 시각에 묶여
 * 있어서 키에 쓰려면 없는 답변 식별자를 지어내야 한다. 지어낸 식별자는 AAD가 무엇을 묶고
 * 있는지를 흐리고, 그러면 봉투를 다른 소유자의 행에 붙여 넣는 실수를 막지 못한다. 그래서 키의
 * AAD는 소유자와 슬롯과 KEK 버전으로 직접 만든다. 키 재료는 같은 provider가 준다.
 */
@Component
class StrongLlmCredentialCrypto(
    private val kekProvider: RagHistoryKekProvider,
    private val secureRandom: SecureRandom = SecureRandom(),
) : StrongLlmCredentialPort {
    override fun seal(
        ownerUserId: String,
        slot: String,
        apiKey: String,
    ): StrongLlmSealedCredential {
        validate(ownerUserId, slot)
        val plaintext = apiKey.toByteArray(StandardCharsets.UTF_8)
        require(plaintext.isNotEmpty() && plaintext.size <= MAX_KEY_BYTES)
        require(apiKey.length >= 8) { "STRONG_LLM_CREDENTIAL_TOO_SHORT" }
        val dek = ByteArray(32).also(secureRandom::nextBytes)
        val kek = kekProvider.current()
        return try {
            require(kek.keyBytes.size == 32)
            val sealedKey = encrypt(dek, plaintext, aad(ownerUserId, slot, kek.version, "key"))
            val wrapped = encrypt(kek.keyBytes, dek, aad(ownerUserId, slot, kek.version, "wrap"))
            StrongLlmSealedCredential(
                kekVersion = kek.version,
                wrapNonce = wrapped.nonce,
                wrappedDek = wrapped.ciphertext,
                wrapTag = wrapped.tag,
                keyNonce = sealedKey.nonce,
                keyCiphertext = sealedKey.ciphertext,
                keyTag = sealedKey.tag,
                // 화면이 "키가 들어 있다"를 말하는 데 필요한 전부다. 그 이상은 저장하지 않는다.
                keyLast4 = apiKey.takeLast(4),
            )
        } finally {
            dek.fill(0)
            kek.keyBytes.fill(0)
            plaintext.fill(0)
        }
    }

    /** provider 호출 직전에만 부른다. 반환한 배열은 호출자가 쓰고 나서 지운다. */
    override fun open(
        ownerUserId: String,
        slot: String,
        sealed: StrongLlmSealedCredential,
    ): ByteArray {
        validate(ownerUserId, slot)
        val kek = kekProvider.byVersion(sealed.kekVersion)
        return try {
            val dek =
                decrypt(
                    kek.keyBytes,
                    sealed.wrapNonce,
                    sealed.wrappedDek,
                    sealed.wrapTag,
                    aad(ownerUserId, slot, sealed.kekVersion, "wrap"),
                )
            try {
                require(dek.size == 32)
                decrypt(
                    dek,
                    sealed.keyNonce,
                    sealed.keyCiphertext,
                    sealed.keyTag,
                    aad(ownerUserId, slot, sealed.kekVersion, "key"),
                )
            } finally {
                dek.fill(0)
            }
        } catch (_: Exception) {
            // 실패 이유를 나누지 않는다. 어느 단계에서 틀렸는지가 곧 봉투에 대한 정보다.
            throw StrongLlmCredentialCorruptedException()
        } finally {
            kek.keyBytes.fill(0)
        }
    }

    private data class Sealed(
        val nonce: ByteArray,
        val ciphertext: ByteArray,
        val tag: ByteArray,
    )

    private fun encrypt(
        key: ByteArray,
        plaintext: ByteArray,
        aad: ByteArray,
    ): Sealed {
        val nonce = ByteArray(NONCE_BYTES).also(secureRandom::nextBytes)
        val cipher = Cipher.getInstance(CIPHER)
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(TAG_BITS, nonce))
        cipher.updateAAD(aad)
        val encrypted = cipher.doFinal(plaintext)
        val split = encrypted.size - TAG_BYTES
        require(split >= 0)
        return Sealed(nonce, encrypted.copyOfRange(0, split), encrypted.copyOfRange(split, encrypted.size))
    }

    private fun decrypt(
        key: ByteArray,
        nonce: ByteArray,
        ciphertext: ByteArray,
        tag: ByteArray,
        aad: ByteArray,
    ): ByteArray {
        require(key.size == 32 && nonce.size == NONCE_BYTES && tag.size == TAG_BYTES)
        require(ciphertext.isNotEmpty() && ciphertext.size <= MAX_KEY_BYTES)
        val cipher = Cipher.getInstance(CIPHER)
        cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(TAG_BITS, nonce))
        cipher.updateAAD(aad)
        return cipher.doFinal(ciphertext + tag)
    }

    private fun aad(
        ownerUserId: String,
        slot: String,
        kekVersion: String,
        field: String,
    ): ByteArray = "strong-llm-credential-v1|$ownerUserId|$slot|$kekVersion|$field".toByteArray(StandardCharsets.UTF_8)

    private fun validate(
        ownerUserId: String,
        slot: String,
    ) {
        require(OWNER_ID.matches(ownerUserId))
        require(slot in setOf("PRIMARY", "FALLBACK"))
    }

    private companion object {
        const val CIPHER = "AES/GCM/NoPadding"
        const val NONCE_BYTES = 12
        const val TAG_BYTES = 16
        const val TAG_BITS = 128
        const val MAX_KEY_BYTES = 8_192
        val OWNER_ID = Regex("^usr_[0-9a-z_]{1,60}$")
    }
}
