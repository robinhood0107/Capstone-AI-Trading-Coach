package com.capstone.decision.infrastructure.brokerage

import org.springframework.context.annotation.Profile
import org.springframework.stereotype.Component
import java.nio.ByteBuffer
import java.nio.charset.StandardCharsets
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

data class SealedBrokerageCredential(
    val kekVersion: String,
    val wrapNonce: ByteArray,
    val wrappedDek: ByteArray,
    val wrapTag: ByteArray,
    val secretNonce: ByteArray,
    val secretCiphertext: ByteArray,
    val secretTag: ByteArray,
    val appKeyLast4: String,
    val accountNoLast4: String,
) : AutoCloseable {
    override fun close() {
        wrapNonce.fill(0)
        wrappedDek.fill(0)
        wrapTag.fill(0)
        secretNonce.fill(0)
        secretCiphertext.fill(0)
        secretTag.fill(0)
    }

    override fun toString(): String = "SealedBrokerageCredential(<redacted>)"
}

class OpenedBrokerageCredential(
    val appKey: ByteArray,
    val appSecret: ByteArray,
    val accountNo: ByteArray,
) : AutoCloseable {
    override fun close() {
        appKey.fill(0)
        appSecret.fill(0)
        accountNo.fill(0)
    }

    override fun toString(): String = "OpenedBrokerageCredential(<redacted>)"
}

/** Owner, mode and opaque account ID are AEAD-associated; ciphertext cannot move across users. */
@Component
@Profile("mars-full")
class BrokerageCredentialCrypto(
    private val kekFile: BrokerageKekFile,
    private val random: SecureRandom = SecureRandom(),
) {
    fun seal(
        ownerUserId: String,
        accountId: String,
        appKey: String,
        appSecret: String,
        accountNo: String,
    ): SealedBrokerageCredential {
        validateIdentity(ownerUserId, accountId)
        require(appKey.length in 8..256 && appKey.matches(Regex("^[A-Za-z0-9._-]+$")))
        require(appSecret.length in 8..512 && appSecret.all { it.code in 33..126 })
        require(accountNo.matches(Regex("^[0-9]{10}$")))
        require(appKey.takeLast(4).matches(Regex("^[A-Za-z0-9_-]{4}$")))
        val keyBytes = appKey.toByteArray(StandardCharsets.US_ASCII)
        val secretBytes = appSecret.toByteArray(StandardCharsets.US_ASCII)
        val accountBytes = accountNo.toByteArray(StandardCharsets.US_ASCII)
        val plaintext =
            ByteBuffer
                .allocate(12 + keyBytes.size + secretBytes.size + accountBytes.size)
                .putInt(keyBytes.size)
                .put(keyBytes)
                .putInt(secretBytes.size)
                .put(secretBytes)
                .putInt(accountBytes.size)
                .put(accountBytes)
                .array()
        val dek = ByteArray(32).also(random::nextBytes)
        var kek: ByteArray? = null
        return try {
            kek = kekFile.load()
            val sealed = encrypt(dek, plaintext, aad(ownerUserId, accountId, "payload"))
            val wrapped = encrypt(requireNotNull(kek), dek, aad(ownerUserId, accountId, "wrap"))
            SealedBrokerageCredential(
                kekVersion = BrokerageKekFile.CURRENT_VERSION,
                wrapNonce = wrapped.nonce,
                wrappedDek = wrapped.ciphertext,
                wrapTag = wrapped.tag,
                secretNonce = sealed.nonce,
                secretCiphertext = sealed.ciphertext,
                secretTag = sealed.tag,
                appKeyLast4 = appKey.takeLast(4),
                accountNoLast4 = accountNo.takeLast(4),
            )
        } finally {
            keyBytes.fill(0)
            secretBytes.fill(0)
            accountBytes.fill(0)
            plaintext.fill(0)
            dek.fill(0)
            kek?.fill(0)
        }
    }

    fun open(
        ownerUserId: String,
        accountId: String,
        sealed: SealedBrokerageCredential,
    ): OpenedBrokerageCredential {
        validateIdentity(ownerUserId, accountId)
        val kek = kekFile.load(sealed.kekVersion)
        return try {
            val dek = decrypt(kek, sealed.wrapNonce, sealed.wrappedDek, sealed.wrapTag, aad(ownerUserId, accountId, "wrap"))
            try {
                require(dek.size == 32)
                val plaintext =
                    decrypt(dek, sealed.secretNonce, sealed.secretCiphertext, sealed.secretTag, aad(ownerUserId, accountId, "payload"))
                try {
                    val buffer = ByteBuffer.wrap(plaintext)
                    var appKey: ByteArray? = null
                    var appSecret: ByteArray? = null
                    var accountNo: ByteArray? = null
                    try {
                        appKey = readPart(buffer, 8, 256)
                        appSecret = readPart(buffer, 8, 512)
                        accountNo = readPart(buffer, 10, 10)
                        require(!buffer.hasRemaining())
                        val opened = OpenedBrokerageCredential(appKey, appSecret, accountNo)
                        appKey = null
                        appSecret = null
                        accountNo = null
                        opened
                    } finally {
                        appKey?.fill(0)
                        appSecret?.fill(0)
                        accountNo?.fill(0)
                    }
                } finally {
                    plaintext.fill(0)
                }
            } finally {
                dek.fill(0)
            }
        } catch (_: Exception) {
            throw IllegalStateException("BROKERAGE_CREDENTIAL_UNAVAILABLE")
        } finally {
            kek.fill(0)
        }
    }

    private fun readPart(
        buffer: ByteBuffer,
        minimum: Int,
        maximum: Int,
    ): ByteArray {
        require(buffer.remaining() >= 4)
        val length = buffer.int
        require(length in minimum..maximum && buffer.remaining() >= length)
        return ByteArray(length).also(buffer::get)
    }

    private data class Ciphertext(
        val nonce: ByteArray,
        val ciphertext: ByteArray,
        val tag: ByteArray,
    )

    private fun encrypt(
        key: ByteArray,
        plaintext: ByteArray,
        aad: ByteArray,
    ): Ciphertext {
        val nonce = ByteArray(12).also(random::nextBytes)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce))
        cipher.updateAAD(aad)
        val encrypted = cipher.doFinal(plaintext)
        return Ciphertext(nonce, encrypted.copyOfRange(0, encrypted.size - 16), encrypted.copyOfRange(encrypted.size - 16, encrypted.size))
    }

    private fun decrypt(
        key: ByteArray,
        nonce: ByteArray,
        ciphertext: ByteArray,
        tag: ByteArray,
        aad: ByteArray,
    ): ByteArray {
        require(key.size == 32 && nonce.size == 12 && tag.size == 16 && ciphertext.isNotEmpty() && ciphertext.size <= 8_192)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce))
        cipher.updateAAD(aad)
        return cipher.doFinal(ciphertext + tag)
    }

    private fun aad(
        ownerUserId: String,
        accountId: String,
        field: String,
    ): ByteArray =
        "mars-brokerage-v1|$ownerUserId|KIS_MOCK|$accountId|${BrokerageKekFile.CURRENT_VERSION}|$field"
            .toByteArray(StandardCharsets.US_ASCII)

    private fun validateIdentity(
        ownerUserId: String,
        accountId: String,
    ) {
        require(ownerUserId.matches(Regex("^usr_[A-Za-z0-9_-]{4,96}$")))
        require(accountId.matches(Regex("^acct_[0-9a-f]{32}$")))
    }
}
