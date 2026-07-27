package com.capstone.decision.infrastructure.brokerage

import com.capstone.decision.application.brokerage.BrokerageUnavailableException
import com.capstone.decision.application.brokerage.paper.PaperClaimLookup
import com.capstone.decision.application.brokerage.paper.PaperIdempotencyClaim
import com.capstone.decision.application.brokerage.paper.PaperIdempotencyClaimPort
import org.springframework.data.redis.core.StringRedisTemplate
import org.springframework.data.redis.core.script.DefaultRedisScript
import org.springframework.stereotype.Component
import java.time.Duration
import java.util.UUID

/**
 * Redis에는 paper purpose HMAC scope와 임의 claim token만 저장한다.
 * 완료 결과는 저장하지 않고 PostgreSQL durable replay 계약을 그대로 사용한다.
 */
@Component
class RedisPaperIdempotencyClaimAdapter(
    private val redisTemplate: StringRedisTemplate,
) : PaperIdempotencyClaimPort {
    override fun acquire(
        scopeHash: String,
        requestHash: String,
    ): PaperClaimLookup {
        require(scopeHash.matches(HASH_PATTERN))
        require(requestHash.matches(HASH_PATTERN))
        val token = UUID.randomUUID().toString()
        val value = "$token:$requestHash"
        val key = claimKey(scopeHash)
        val acquired =
            try {
                redisTemplate.opsForValue().setIfAbsent(key, value, CLAIM_TTL) == true
            } catch (exception: RuntimeException) {
                throw BrokerageUnavailableException("Paper idempotency claim store is unavailable.", exception)
            }
        if (acquired) {
            return PaperClaimLookup.Acquired(PaperIdempotencyClaim(scopeHash, requestHash, token))
        }
        val existingHash =
            try {
                redisTemplate.opsForValue().get(key)?.substringAfter(':', missingDelimiterValue = "")
            } catch (exception: RuntimeException) {
                throw BrokerageUnavailableException("Paper idempotency claim store is unavailable.", exception)
            }
        return if (!existingHash.isNullOrEmpty() && existingHash != requestHash) {
            PaperClaimLookup.Conflict
        } else {
            PaperClaimLookup.InProgress
        }
    }

    override fun release(claim: PaperIdempotencyClaim) {
        redisTemplate.execute(
            RELEASE_SCRIPT,
            listOf(claimKey(claim.scopeHash)),
            "${claim.token}:${claim.requestHash}",
        )
    }

    internal fun claimKey(scopeHash: String): String = "$CLAIM_PREFIX$scopeHash"

    companion object {
        const val CLAIM_PREFIX = "brokerage-paper-idempotency:claim:"
        private val CLAIM_TTL = Duration.ofSeconds(30)
        private val HASH_PATTERN = Regex("^[0-9a-f]{64}$")
        private val RELEASE_SCRIPT =
            DefaultRedisScript(
                """
                if redis.call('GET', KEYS[1]) ~= ARGV[1] then
                    return 0
                end
                return redis.call('DEL', KEYS[1])
                """.trimIndent(),
                Long::class.java,
            )
    }
}
