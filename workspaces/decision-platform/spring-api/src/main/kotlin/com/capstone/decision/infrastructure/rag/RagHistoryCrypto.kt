package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagDecryptedHistoryPayload
import com.capstone.decision.application.rag.RagEncryptedFieldPayload
import com.capstone.decision.application.rag.RagEncryptedHistoryPayload
import com.capstone.decision.application.rag.RagHistoryCorruptedException
import com.capstone.decision.application.rag.RagHistoryCryptoPort
import com.capstone.decision.application.rag.RagHistoryIdentity
import org.springframework.stereotype.Component
import java.nio.charset.StandardCharsets
import java.security.GeneralSecurityException
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

data class RagHistoryKek(
    val version: String,
    val keyBytes: ByteArray,
)

interface RagHistoryKekProvider {
    fun current(): RagHistoryKek

    fun byVersion(version: String): RagHistoryKek
}

class InMemoryRagHistoryKekProvider(
    private val currentVersion: String,
    keys: Map<String, ByteArray>,
) : RagHistoryKekProvider {
    private val keys = keys.mapValues { (_, value) -> value.copyOf() }

    init {
        require(currentVersion in this.keys)
        require(this.keys.values.all { it.size == 32 })
    }

    override fun current(): RagHistoryKek = byVersion(currentVersion)

    override fun byVersion(version: String): RagHistoryKek {
        val key = keys[version] ?: throw RagHistoryCorruptedException()
        return RagHistoryKek(version, key.copyOf())
    }
}

@Component
class RagHistoryCrypto(
    private val kekProvider: RagHistoryKekProvider,
    private val secureRandom: SecureRandom = SecureRandom(),
) : RagHistoryCryptoPort {
    /**
     * answer row마다 새 DEK와 field별 nonce를 생성하고, 질문·답변은 exact AAD로 독립 암호화한다.
     */
    override fun encrypt(
        identity: RagHistoryIdentity,
        question: String,
        answer: String,
    ): RagEncryptedHistoryPayload {
        validateIdentity(identity)
        require(question.toByteArray(StandardCharsets.UTF_8).size <= MAX_FIELD_BYTES)
        require(answer.toByteArray(StandardCharsets.UTF_8).size <= MAX_FIELD_BYTES)
        val dek = ByteArray(32).also(secureRandom::nextBytes)
        val currentKek = kekProvider.current()
        return try {
            require(currentKek.keyBytes.size == 32)
            val questionField = encryptField(dek, question, aad(identity, "question"))
            val answerField = encryptField(dek, answer, aad(identity, "answer"))
            val wrapped = encryptBytes(currentKek.keyBytes, dek, wrapAad(identity, currentKek.version))
            RagEncryptedHistoryPayload(
                kekVersion = currentKek.version,
                wrapNonce = wrapped.nonce,
                wrappedDek = wrapped.ciphertext,
                wrapTag = wrapped.tag,
                question = questionField,
                answer = answerField,
            )
        } finally {
            dek.fill(0)
            currentKek.keyBytes.fill(0)
        }
    }

    override fun decrypt(
        identity: RagHistoryIdentity,
        encrypted: RagEncryptedHistoryPayload,
    ): RagDecryptedHistoryPayload =
        try {
            validateIdentity(identity)
            validateEncrypted(encrypted)
            val kek = kekProvider.byVersion(encrypted.kekVersion)
            try {
                val dek =
                    decryptBytes(
                        key = kek.keyBytes,
                        field =
                            RagEncryptedFieldPayload(
                                nonce = encrypted.wrapNonce,
                                ciphertext = encrypted.wrappedDek,
                                tag = encrypted.wrapTag,
                            ),
                        aad = wrapAad(identity, encrypted.kekVersion),
                    )
                try {
                    require(dek.size == 32)
                    RagDecryptedHistoryPayload(
                        question =
                            decryptBytes(
                                dek,
                                encrypted.question,
                                aad(identity, "question"),
                            ).decodeUtf8(),
                        answer =
                            decryptBytes(
                                dek,
                                encrypted.answer,
                                aad(identity, "answer"),
                            ).decodeUtf8(),
                    )
                } finally {
                    dek.fill(0)
                }
            } finally {
                kek.keyBytes.fill(0)
            }
        } catch (_: RagHistoryCorruptedException) {
            throw RagHistoryCorruptedException()
        } catch (_: GeneralSecurityException) {
            throw RagHistoryCorruptedException()
        } catch (_: IllegalArgumentException) {
            throw RagHistoryCorruptedException()
        }

    fun aad(
        identity: RagHistoryIdentity,
        fieldName: String,
    ): ByteArray {
        require(fieldName in setOf("question", "answer"))
        return (
            "rag-history-v1|${identity.answerId}|${identity.ownerUserId}|" +
                "${identity.createdAt.toEpochMilli()}|$fieldName"
        ).toByteArray(StandardCharsets.UTF_8)
    }

    private fun wrapAad(
        identity: RagHistoryIdentity,
        version: String,
    ): ByteArray =
        (
            "rag-history-key-v1|${identity.answerId}|${identity.ownerUserId}|" +
                "${identity.createdAt.toEpochMilli()}|$version"
        ).toByteArray(StandardCharsets.UTF_8)

    private fun encryptField(
        dek: ByteArray,
        plaintext: String,
        aad: ByteArray,
    ): RagEncryptedFieldPayload =
        encryptBytes(
            key = dek,
            plaintext = plaintext.toByteArray(StandardCharsets.UTF_8),
            aad = aad,
        )

    private fun encryptBytes(
        key: ByteArray,
        plaintext: ByteArray,
        aad: ByteArray,
    ): RagEncryptedFieldPayload {
        val nonce = ByteArray(NONCE_BYTES).also(secureRandom::nextBytes)
        val cipher = Cipher.getInstance(CIPHER)
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(TAG_BITS, nonce))
        cipher.updateAAD(aad)
        val encrypted = cipher.doFinal(plaintext)
        val split = encrypted.size - TAG_BYTES
        require(split >= 0)
        return RagEncryptedFieldPayload(
            nonce = nonce,
            ciphertext = encrypted.copyOfRange(0, split),
            tag = encrypted.copyOfRange(split, encrypted.size),
        )
    }

    private fun decryptBytes(
        key: ByteArray,
        field: RagEncryptedFieldPayload,
        aad: ByteArray,
    ): ByteArray {
        require(key.size == 32)
        require(field.nonce.size == NONCE_BYTES && field.tag.size == TAG_BYTES)
        require(field.ciphertext.size <= MAX_FIELD_BYTES)
        val cipher = Cipher.getInstance(CIPHER)
        cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(TAG_BITS, field.nonce))
        cipher.updateAAD(aad)
        return cipher.doFinal(field.ciphertext + field.tag)
    }

    private fun validateEncrypted(encrypted: RagEncryptedHistoryPayload) {
        require(RagGuardHistoryProperties.KEK_VERSION.matches(encrypted.kekVersion))
        require(encrypted.wrapNonce.size == NONCE_BYTES)
        require(encrypted.wrapTag.size == TAG_BYTES)
        require(encrypted.wrappedDek.size == 32)
        listOf(encrypted.question, encrypted.answer).forEach { field ->
            require(field.nonce.size == NONCE_BYTES)
            require(field.tag.size == TAG_BYTES)
            require(field.ciphertext.size <= MAX_FIELD_BYTES)
        }
    }

    private fun validateIdentity(identity: RagHistoryIdentity) {
        require(HISTORY_ANSWER_ID.matches(identity.answerId))
        require(OWNER_ID.matches(identity.ownerUserId))
        require(identity.createdAt.toEpochMilli() > 0)
    }

    private fun ByteArray.decodeUtf8(): String {
        val decoded = toString(StandardCharsets.UTF_8)
        require(decoded.toByteArray(StandardCharsets.UTF_8).contentEquals(this))
        return decoded
    }

    private companion object {
        const val CIPHER = "AES/GCM/NoPadding"
        const val NONCE_BYTES = 12
        const val TAG_BYTES = 16
        const val TAG_BITS = 128
        const val MAX_FIELD_BYTES = 8_192
        val HISTORY_ANSWER_ID = Regex("^rag_[A-Za-z0-9_-]{12,96}$")
        val OWNER_ID = Regex("^[A-Za-z0-9._:-]{1,128}$")
    }
}
