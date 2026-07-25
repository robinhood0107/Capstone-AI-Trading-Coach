package com.capstone.decision.infrastructure.decision

import com.capstone.decision.application.decision.DecisionClaimLookup
import com.capstone.decision.application.decision.DecisionIdempotencyClaim
import com.capstone.decision.application.decision.DecisionIdempotencyClaimPort
import com.capstone.decision.application.decision.DecisionIdempotencyIdentity
import com.capstone.decision.application.decision.DecisionIdempotencyIdentityPort
import com.capstone.decision.application.decision.EvaluateOrderCommand
import com.capstone.decision.domain.risk.CanonicalJson
import org.springframework.data.redis.core.StringRedisTemplate
import org.springframework.data.redis.core.script.DefaultRedisScript
import org.springframework.stereotype.Component
import java.nio.charset.StandardCharsets
import java.time.Duration
import java.util.UUID
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * raw actor/key를 저장·로그·metric label로 만들지 않고 purpose-version HMAC과 canonical request hash만 생성한다.
 */
@Component
class DecisionIdempotencyHasher(
    private val properties: DecisionProperties,
) : DecisionIdempotencyIdentityPort {
    override fun identity(
        actorUserId: String,
        rawKey: String,
        command: EvaluateOrderCommand,
    ): DecisionIdempotencyIdentity =
        DecisionIdempotencyIdentity(
            scopeHash =
                hmac(
                    listOf(
                        DecisionProperties.PURPOSE_VERSION,
                        "scope",
                        actorUserId,
                        rawKey,
                    ),
                ),
            ownerScopeHash =
                hmac(
                    listOf(
                        DecisionProperties.PURPOSE_VERSION,
                        "owner",
                        actorUserId,
                    ),
                ),
            requestHash = requestHash(command),
        )

    private fun requestHash(command: EvaluateOrderCommand): String =
        CanonicalJson.sha256(
            CanonicalJson.encode(
                mapOf(
                    "orderIntent" to
                        mapOf(
                            "estimatedAmount" to command.orderIntent.estimatedAmount.toString(),
                            "estimatedPrice" to command.orderIntent.estimatedPrice.toString(),
                            "orderType" to command.orderIntent.orderType,
                            "quantity" to command.orderIntent.quantity.toString(),
                            "side" to command.orderIntent.side,
                            "strategyId" to command.orderIntent.strategyId,
                            "symbol" to command.orderIntent.symbol,
                            "timeframe" to command.orderIntent.timeframe,
                        ),
                    "portfolioSource" to command.portfolioSource,
                    "principleId" to command.principleId.value,
                ),
            ),
        )

    private fun hmac(parts: List<String>): String {
        val keyBytes = properties.idempotencyScopeHmacKey.toByteArray(StandardCharsets.UTF_8)
        val messageBytes = parts.joinToString(separator = "\u0000").toByteArray(StandardCharsets.UTF_8)
        return try {
            val mac = Mac.getInstance("HmacSHA256")
            mac.init(SecretKeySpec(keyBytes, "HmacSHA256"))
            mac.doFinal(messageBytes).joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
        } finally {
            keyBytes.fill(0)
            messageBytes.fill(0)
        }
    }
}

/**
 * Redis에는 stable 64-hex scope와 random claim token만 두며 durable response는 PostgreSQL이 소유한다.
 */
@Component
class DecisionIdempotencyClaimService(
    private val redisTemplate: StringRedisTemplate,
    private val properties: DecisionProperties,
) : DecisionIdempotencyClaimPort {
    override fun acquire(
        scopeHash: String,
        requestHash: String,
    ): DecisionClaimLookup {
        require(SHA256.matches(scopeHash) && SHA256.matches(requestHash))
        val token = UUID.randomUUID().toString().replace("-", "")
        val value = "$token:$requestHash"
        val key = claimKey(scopeHash)
        val acquired =
            redisTemplate
                .opsForValue()
                .setIfAbsent(key, value, Duration.ofSeconds(properties.claimTtlSeconds)) == true
        if (acquired) {
            return DecisionClaimLookup.Acquired(
                DecisionIdempotencyClaim(
                    scopeHash = scopeHash,
                    requestHash = requestHash,
                    token = token,
                ),
            )
        }
        val existing = redisTemplate.opsForValue().get(key) ?: return DecisionClaimLookup.InProgress
        val existingHash = existing.substringAfter(':', missingDelimiterValue = "")
        if (!SHA256.matches(existingHash)) {
            error("Decision idempotency claim violated its bounded internal contract.")
        }
        return if (existingHash == requestHash) {
            DecisionClaimLookup.InProgress
        } else {
            DecisionClaimLookup.Conflict
        }
    }

    override fun release(claim: DecisionIdempotencyClaim) {
        redisTemplate.execute(
            RELEASE_SCRIPT,
            listOf(claimKey(claim.scopeHash)),
            "${claim.token}:${claim.requestHash}",
        )
    }

    private fun claimKey(scopeHash: String): String = "decision-idempotency:claim:$scopeHash"

    private companion object {
        val SHA256 = Regex("^[0-9a-f]{64}$")
        val RELEASE_SCRIPT =
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
