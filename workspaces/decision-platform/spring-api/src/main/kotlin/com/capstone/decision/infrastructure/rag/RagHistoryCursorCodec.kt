package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagFieldViolation
import com.capstone.decision.application.rag.RagHistoryCursorPoint
import com.capstone.decision.application.rag.RagHistoryCursorPort
import com.capstone.decision.application.rag.RagValidationException
import org.springframework.stereotype.Component
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Instant
import java.util.Base64
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

@Component
class RagHistoryCursorCodec(
    private val properties: RagGuardHistoryProperties,
) : RagHistoryCursorPort {
    override fun encode(
        ownerUserId: String,
        point: RagHistoryCursorPoint,
    ): String {
        require(OWNER.matches(ownerUserId))
        require(ANSWER_ID.matches(point.answerId))
        val payload = "${point.createdAt.toEpochMilli()}|${point.answerId}"
        val signature = signature(ownerUserId, payload)
        return Base64
            .getUrlEncoder()
            .withoutPadding()
            .encodeToString("$payload|$signature".toByteArray(StandardCharsets.US_ASCII))
    }

    override fun decode(
        ownerUserId: String,
        cursor: String,
    ): RagHistoryCursorPoint {
        try {
            require(OWNER.matches(ownerUserId))
            require(cursor.length in 1..512 && CURSOR.matches(cursor))
            val decoded = Base64.getUrlDecoder().decode(cursor).toString(StandardCharsets.US_ASCII)
            val parts = decoded.split('|')
            require(parts.size == 3)
            val epochMillis = parts[0].toLong()
            val answerId = parts[1]
            val supplied = parts[2]
            require(epochMillis > 0 && ANSWER_ID.matches(answerId) && SHA256.matches(supplied))
            val payload = "$epochMillis|$answerId"
            val expected = signature(ownerUserId, payload)
            require(
                MessageDigest.isEqual(
                    supplied.toByteArray(StandardCharsets.US_ASCII),
                    expected.toByteArray(StandardCharsets.US_ASCII),
                ),
            )
            val point = RagHistoryCursorPoint(Instant.ofEpochMilli(epochMillis), answerId)
            require(encode(ownerUserId, point) == cursor)
            return point
        } catch (_: Exception) {
            throw RagValidationException(
                listOf(RagFieldViolation("/query/cursor", "INVALID_CURSOR")),
            )
        }
    }

    private fun signature(
        ownerUserId: String,
        payload: String,
    ): String {
        val key = properties.historyCursorHmacKey.toByteArray(StandardCharsets.UTF_8)
        val message =
            listOf(
                RagGuardHistoryProperties.CURSOR_PURPOSE,
                ownerUserId,
                payload,
            ).joinToString("\u0000").toByteArray(StandardCharsets.UTF_8)
        return try {
            val mac = Mac.getInstance("HmacSHA256")
            mac.init(SecretKeySpec(key, "HmacSHA256"))
            mac.doFinal(message).joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
        } finally {
            key.fill(0)
            message.fill(0)
        }
    }

    private companion object {
        val OWNER = Regex("^[A-Za-z0-9._:-]{1,128}$")
        val ANSWER_ID = Regex("^rag_(?:ans_[0-9a-f]{32}|[A-Za-z0-9_-]{12,96})$")
        val SHA256 = Regex("^[0-9a-f]{64}$")
        val CURSOR = Regex("^[A-Za-z0-9_-]{1,512}$")
    }
}
