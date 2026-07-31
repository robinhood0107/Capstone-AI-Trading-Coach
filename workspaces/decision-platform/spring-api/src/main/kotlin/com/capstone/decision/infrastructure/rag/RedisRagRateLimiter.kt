package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagGuardHistoryUnavailableException
import com.capstone.decision.application.rag.RagRateLimitPort
import com.capstone.decision.application.rag.RagRateLimitedException
import org.springframework.data.redis.core.StringRedisTemplate
import org.springframework.data.redis.core.script.DefaultRedisScript
import org.springframework.stereotype.Component
import java.nio.charset.StandardCharsets
import java.time.Clock
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

@Component
class RedisRagRateLimiter(
    private val redisTemplate: StringRedisTemplate,
    private val properties: RagGuardHistoryProperties,
    private val principleClock: Clock,
) : RagRateLimitPort {
    /**
     * Redis에는 owner/question이 아닌 minute bucket별 RAG 전용 HMAC key와 bounded counter만 둔다.
     */
    override fun acquire(ownerUserId: String) {
        require(ownerUserId.isNotBlank() && ownerUserId.length <= 128)
        val minuteBucket = principleClock.instant().epochSecond / 60
        val scope = scope(ownerUserId, minuteBucket)
        val count =
            try {
                redisTemplate.execute(
                    ACQUIRE_SCRIPT,
                    listOf("rag-rate:v1:$scope"),
                    WINDOW_MILLIS.toString(),
                )
            } catch (exception: RuntimeException) {
                throw RagGuardHistoryUnavailableException(exception)
            } ?: throw RagGuardHistoryUnavailableException()
        if (count > properties.rateLimitPerMinute) {
            throw RagRateLimitedException()
        }
    }

    private fun scope(
        ownerUserId: String,
        minuteBucket: Long,
    ): String {
        val key = properties.rateLimitHmacKey.toByteArray(StandardCharsets.UTF_8)
        val message =
            listOf(
                RagGuardHistoryProperties.RATE_PURPOSE,
                ownerUserId,
                minuteBucket.toString(),
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
        const val WINDOW_MILLIS = 61_000L
        val ACQUIRE_SCRIPT =
            DefaultRedisScript(
                """
                local count = redis.call('INCR', KEYS[1])
                if count == 1 then
                    redis.call('PEXPIRE', KEYS[1], ARGV[1])
                end
                return count
                """.trimIndent(),
                Long::class.java,
            )
    }
}
