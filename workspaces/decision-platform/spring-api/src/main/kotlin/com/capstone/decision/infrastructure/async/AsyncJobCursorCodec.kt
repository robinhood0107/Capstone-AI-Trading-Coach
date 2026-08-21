package com.capstone.decision.infrastructure.async

import com.capstone.decision.application.async.AsyncJobStatus
import com.capstone.decision.application.async.AsyncJobType
import org.springframework.stereotype.Component
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.util.Base64
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

data class AsyncJobCursor(
    val beforeRequestedAt: Instant,
    val beforeJobId: String,
)

class InvalidAsyncJobCursorException : RuntimeException("Invalid async job cursor.")

@Component
class AsyncJobCursorCodec(
    properties: AsyncProperties,
    private val clock: Clock = Clock.systemUTC(),
) {
    private val key = properties.cursorHmacKey.toByteArray(StandardCharsets.UTF_8)

    fun encode(
        actorUserId: String,
        status: AsyncJobStatus?,
        type: AsyncJobType?,
        size: Int,
        beforeRequestedAt: Instant,
        beforeJobId: String,
    ): String {
        require(size in 1..100)
        require(JOB_ID.matches(beforeJobId))
        val payload =
            ByteArrayOutputStream().use { bytes ->
                DataOutputStream(bytes).use { output ->
                    output.writeByte(VERSION)
                    output.writeLong(clock.instant().plus(TTL).epochSecond)
                    output.writeLong(beforeRequestedAt.toEpochMilli())
                    output.writeByte(size)
                    output.writeByte(status?.ordinal?.plus(1) ?: 0)
                    output.writeByte(type?.ordinal?.plus(1) ?: 0)
                    output.write(actorBinding(actorUserId))
                    output.writeUTF(beforeJobId)
                }
                bytes.toByteArray()
            }
        val signed = payload + signature(payload)
        return Base64.getUrlEncoder().withoutPadding().encodeToString(signed).also {
            require(it.length in 16..256)
        }
    }

    fun decode(
        value: String,
        actorUserId: String,
        status: AsyncJobStatus?,
        type: AsyncJobType?,
        size: Int,
    ): AsyncJobCursor {
        try {
            require(value.length in 16..256 && CURSOR.matches(value))
            val signed = Base64.getUrlDecoder().decode(value)
            require(signed.size > SIGNATURE_BYTES)
            val payload = signed.copyOfRange(0, signed.size - SIGNATURE_BYTES)
            val suppliedSignature = signed.copyOfRange(signed.size - SIGNATURE_BYTES, signed.size)
            require(MessageDigest.isEqual(signature(payload), suppliedSignature))
            return DataInputStream(ByteArrayInputStream(payload)).use { input ->
                require(input.readUnsignedByte() == VERSION)
                val expiresAt = Instant.ofEpochSecond(input.readLong())
                require(expiresAt.isAfter(clock.instant()) && !expiresAt.isAfter(clock.instant().plus(TTL)))
                val requestedAt = Instant.ofEpochMilli(input.readLong())
                require(input.readUnsignedByte() == size)
                require(input.readUnsignedByte() == (status?.ordinal?.plus(1) ?: 0))
                require(input.readUnsignedByte() == (type?.ordinal?.plus(1) ?: 0))
                val suppliedActor = input.readNBytes(ACTOR_BINDING_BYTES)
                require(suppliedActor.size == ACTOR_BINDING_BYTES)
                require(MessageDigest.isEqual(actorBinding(actorUserId), suppliedActor))
                val jobId = input.readUTF()
                require(JOB_ID.matches(jobId) && input.available() == 0)
                AsyncJobCursor(requestedAt, jobId)
            }
        } catch (_: Exception) {
            throw InvalidAsyncJobCursorException()
        }
    }

    private fun actorBinding(actorUserId: String): ByteArray =
        MessageDigest.getInstance("SHA-256").digest(actorUserId.toByteArray(StandardCharsets.UTF_8)).copyOf(ACTOR_BINDING_BYTES)

    private fun signature(payload: ByteArray): ByteArray =
        Mac
            .getInstance(HMAC_SHA256)
            .apply { init(SecretKeySpec(key, HMAC_SHA256)) }
            .doFinal(payload)
            .copyOf(SIGNATURE_BYTES)

    private companion object {
        const val VERSION = 1
        const val ACTOR_BINDING_BYTES = 16
        const val SIGNATURE_BYTES = 16
        const val HMAC_SHA256 = "HmacSHA256"
        val TTL: Duration = Duration.ofMinutes(5)
        val JOB_ID = Regex("^job_[A-Za-z0-9_-]{8,96}$")
        val CURSOR = Regex("^[A-Za-z0-9_-]{16,256}$")
    }
}
