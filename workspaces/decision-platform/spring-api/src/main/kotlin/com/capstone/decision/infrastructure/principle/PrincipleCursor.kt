package com.capstone.decision.infrastructure.principle

import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.stereotype.Component
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Clock
import java.time.OffsetDateTime
import java.util.Base64
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

@ConfigurationProperties("app.principle")
data class PrincipleProperties(
    var cursorHmacKey: String = "",
) {
    fun validate() {
        require(cursorHmacKey.toByteArray(StandardCharsets.UTF_8).size >= 32) {
            "app.principle.cursor-hmac-key must be at least 32 bytes."
        }
    }
}

@Configuration(proxyBeanMethods = false)
class PrincipleConfiguration {
    @Bean
    fun principleClock(): Clock = Clock.systemUTC()
}

// cursor는 signature 검증 전 payload를 사용하지 않고 route/subject/resource/sort/size를 모두 다시 묶는다.
@Component
class PrincipleCursorCodec(
    private val properties: PrincipleProperties,
    private val catalog: PrincipleCatalog,
    private val principleClock: Clock,
) {
    init {
        properties.validate()
    }

    fun encodeOwner(
        userId: String,
        size: Int,
        sort: String,
        updatedAt: OffsetDateTime,
        principleId: String,
    ): String =
        encode(
            CursorPayload(
                purpose = OWNER_PURPOSE,
                subjectBinding = subjectBinding(userId),
                resource = NO_RESOURCE,
                sort = sort,
                size = size,
                position = updatedAt.toInstant().toString(),
                tieBreaker = principleId,
                expiresAtEpochSecond = principleClock.instant().epochSecond + catalog.cursorTtlSeconds,
            ),
        )

    fun decodeOwner(
        cursor: String,
        userId: String,
        requestedSize: Int?,
        requestedSort: String?,
    ): OwnerCursor {
        val payload = decode(cursor)
        requireBinding(payload, OWNER_PURPOSE, userId, NO_RESOURCE, requestedSize, requestedSort)
        return OwnerCursor(
            size = payload.size,
            sort = payload.sort,
            updatedAt = OffsetDateTime.parse(payload.position),
            principleId = payload.tieBreaker,
        )
    }

    fun encodeHistory(
        userId: String,
        principleId: String,
        size: Int,
        sort: String,
        version: Int,
    ): String =
        encode(
            CursorPayload(
                purpose = HISTORY_PURPOSE,
                subjectBinding = subjectBinding(userId),
                resource = principleId,
                sort = sort,
                size = size,
                position = version.toString(),
                tieBreaker = NO_RESOURCE,
                expiresAtEpochSecond = principleClock.instant().epochSecond + catalog.cursorTtlSeconds,
            ),
        )

    fun decodeHistory(
        cursor: String,
        userId: String,
        principleId: String,
        requestedSize: Int?,
        requestedSort: String?,
    ): HistoryCursor {
        val payload = decode(cursor)
        requireBinding(payload, HISTORY_PURPOSE, userId, principleId, requestedSize, requestedSort)
        return HistoryCursor(
            size = payload.size,
            sort = payload.sort,
            version = payload.position.toInt(),
        )
    }

    private fun encode(payload: CursorPayload): String {
        val canonical =
            listOf(
                CURSOR_VERSION,
                payload.purpose,
                payload.subjectBinding,
                payload.resource,
                payload.sort,
                payload.size.toString(),
                payload.position,
                payload.tieBreaker,
                payload.expiresAtEpochSecond.toString(),
            ).joinToString(SEPARATOR)
        val payloadPart = ENCODER.encodeToString(canonical.toByteArray(StandardCharsets.UTF_8))
        val signature = ENCODER.encodeToString(mac(SIGNATURE_PURPOSE, payloadPart))
        return "$payloadPart.$signature".also {
            check(it.length <= catalog.cursorMaxChars) { "Generated Principle cursor exceeded its contract limit." }
        }
    }

    private fun decode(cursor: String): CursorPayload {
        try {
            require(cursor.length in 1..catalog.cursorMaxChars)
            val parts = cursor.split('.')
            require(parts.size == 2 && parts.all(String::isNotEmpty))
            val expected = mac(SIGNATURE_PURPOSE, parts[0])
            val actual = DECODER.decode(parts[1])
            require(MessageDigest.isEqual(expected, actual))

            val fields =
                String(DECODER.decode(parts[0]), StandardCharsets.UTF_8)
                    .split(SEPARATOR)
            require(fields.size == PAYLOAD_FIELD_COUNT)
            require(fields[0] == CURSOR_VERSION)
            val size = fields[5].toInt()
            val expiresAt = fields[8].toLong()
            require(size in catalog.pageMin..catalog.pageMax)
            require(expiresAt > principleClock.instant().epochSecond)
            return CursorPayload(
                purpose = fields[1],
                subjectBinding = fields[2],
                resource = fields[3],
                sort = fields[4],
                size = size,
                position = fields[6],
                tieBreaker = fields[7],
                expiresAtEpochSecond = expiresAt,
            )
        } catch (_: RuntimeException) {
            throw InvalidPrincipleCursorException()
        }
    }

    private fun requireBinding(
        payload: CursorPayload,
        purpose: String,
        userId: String,
        resource: String,
        requestedSize: Int?,
        requestedSort: String?,
    ) {
        try {
            require(payload.purpose == purpose)
            require(MessageDigest.isEqual(payload.subjectBinding.toByteArray(), subjectBinding(userId).toByteArray()))
            require(payload.resource == resource)
            require(requestedSize == null || requestedSize == payload.size)
            require(requestedSort == null || requestedSort == payload.sort)
        } catch (_: IllegalArgumentException) {
            throw InvalidPrincipleCursorException()
        }
    }

    private fun subjectBinding(userId: String): String = ENCODER.encodeToString(mac(SUBJECT_PURPOSE, userId))

    private fun mac(
        purpose: String,
        value: String,
    ): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(properties.cursorHmacKey.toByteArray(StandardCharsets.UTF_8), "HmacSHA256"))
        mac.update("capstone-principle-cursor\u0000$CURSOR_VERSION\u0000$purpose\u0000".toByteArray(StandardCharsets.UTF_8))
        return mac.doFinal(value.toByteArray(StandardCharsets.UTF_8))
    }

    private data class CursorPayload(
        val purpose: String,
        val subjectBinding: String,
        val resource: String,
        val sort: String,
        val size: Int,
        val position: String,
        val tieBreaker: String,
        val expiresAtEpochSecond: Long,
    )

    companion object {
        private const val CURSOR_VERSION = "v1"
        private const val OWNER_PURPOSE = "owner-list"
        private const val HISTORY_PURPOSE = "history"
        private const val SUBJECT_PURPOSE = "subject"
        private const val SIGNATURE_PURPOSE = "signature"
        private const val NO_RESOURCE = "-"
        private const val SEPARATOR = "\n"
        private const val PAYLOAD_FIELD_COUNT = 9
        private val ENCODER = Base64.getUrlEncoder().withoutPadding()
        private val DECODER = Base64.getUrlDecoder()
    }
}

data class OwnerCursor(
    val size: Int,
    val sort: String,
    val updatedAt: OffsetDateTime,
    val principleId: String,
)

data class HistoryCursor(
    val size: Int,
    val sort: String,
    val version: Int,
)

class InvalidPrincipleCursorException : RuntimeException("Invalid Principle cursor.")
