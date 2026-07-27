package com.capstone.decision.infrastructure.idempotency

import com.capstone.decision.infrastructure.brokerage.BrokerageIdempotencyHasher
import com.capstone.decision.infrastructure.brokerage.BrokerageReplayIdentity
import com.capstone.decision.infrastructure.brokerage.BrokerageWriteReplayPurpose
import org.springframework.data.redis.core.StringRedisTemplate
import org.springframework.data.redis.core.script.DefaultRedisScript
import org.springframework.http.MediaType
import org.springframework.stereotype.Service
import java.time.Duration
import java.util.UUID

// SET NX claim을 controller 실행 전에 확보해 같은 key의 동시 요청이 부작용을 두 번 만들지 못하게 한다.
@Service
class IdempotencyService(
    private val redisTemplate: StringRedisTemplate,
    private val properties: IdempotencyProperties,
    private val scopeHasher: BrokerageIdempotencyHasher,
) {
    fun acquire(
        userId: String,
        idempotencyKey: String,
        requestHash: String,
        purpose: BrokerageWriteReplayPurpose,
    ): IdempotencyLookup {
        val identity = scopeHasher.replayIdentity(userId, idempotencyKey, purpose)
        val redisKey = redisKey(identity)
        completedLookup(redisKey, requestHash)?.let { return it }

        val claimKey = claimKey(identity)
        val admissionKey = admissionKey(identity)
        val claimToken = UUID.randomUUID().toString()
        val claimValue = claimValue(claimToken, requestHash)
        val claimed =
            redisTemplate.execute(
                ACQUIRE_CLAIM_SCRIPT,
                listOf(claimKey, admissionKey),
                claimValue,
                properties.claimTtlSeconds.toString(),
                properties.maxNewKeysPerUserPerTtl.toString(),
                Duration.ofHours(properties.ttlHours).seconds.toString(),
            ) ?: 0L
        if (claimed == 1L) {
            // result 저장과 claim 삭제 사이 경합에서 새 claim을 잡았더라도 completed result를 다시 확인한다.
            completedLookup(redisKey, requestHash)?.let { completed ->
                discardClaim(identity.ownerScopeHash, claimKey, claimValue)
                return completed
            }
            return IdempotencyLookup.New(claimToken)
        }
        if (claimed == -1L) {
            return IdempotencyLookup.CapacityExceeded
        }

        val claimedHash = redisTemplate.opsForValue().get(claimKey)?.substringAfter(':', missingDelimiterValue = "")
        return if (!claimedHash.isNullOrEmpty() && claimedHash != requestHash) {
            IdempotencyLookup.Conflict
        } else {
            IdempotencyLookup.InProgress
        }
    }

    fun store(
        userId: String,
        idempotencyKey: String,
        requestHash: String,
        claimToken: String,
        status: Int,
        body: String,
        contentType: String,
        purpose: BrokerageWriteReplayPurpose,
    ) {
        val identity = scopeHasher.replayIdentity(userId, idempotencyKey, purpose)
        val redisKey = redisKey(identity)
        val claimKey = claimKey(identity)
        val stored =
            redisTemplate.execute(
                STORE_RESULT_SCRIPT,
                listOf(redisKey, claimKey),
                claimValue(claimToken, requestHash),
                requestHash,
                status.toString(),
                body,
                safeContentType(contentType),
                Duration.ofHours(properties.ttlHours).seconds.toString(),
            ) == 1L
        if (!stored) {
            // 만료 후 새 owner가 생긴 경우 stale 요청이 결과를 덮거나 새 claim을 지우지 못하게 한다.
            throw IdempotencyClaimLostException()
        }
    }

    fun redisKey(
        userId: String,
        idempotencyKey: String,
        purpose: BrokerageWriteReplayPurpose,
    ): String = redisKey(scopeHasher.replayIdentity(userId, idempotencyKey, purpose))

    fun discard(
        userId: String,
        idempotencyKey: String,
        requestHash: String,
        claimToken: String,
        purpose: BrokerageWriteReplayPurpose,
    ) {
        val identity = scopeHasher.replayIdentity(userId, idempotencyKey, purpose)
        discardClaim(
            ownerScopeHash = identity.ownerScopeHash,
            claimKey = claimKey(identity),
            claimValue = claimValue(claimToken, requestHash),
        )
    }

    private fun redisKey(identity: BrokerageReplayIdentity): String = "idempotency:${identity.scopeHash}"

    private fun claimKey(identity: BrokerageReplayIdentity): String = "idempotency-claim:${identity.scopeHash}"

    private fun admissionKey(identity: BrokerageReplayIdentity): String = admissionKey(identity.ownerScopeHash)

    private fun admissionKey(ownerScopeHash: String): String = "idempotency-admission:$ownerScopeHash"

    private fun claimValue(
        claimToken: String,
        requestHash: String,
    ): String = "$claimToken:$requestHash"

    private fun discardClaim(
        ownerScopeHash: String,
        claimKey: String,
        claimValue: String,
    ) {
        redisTemplate.execute(
            DISCARD_CLAIM_SCRIPT,
            listOf(claimKey, admissionKey(ownerScopeHash)),
            claimValue,
        )
    }

    private fun completedLookup(
        redisKey: String,
        requestHash: String,
    ): IdempotencyLookup? {
        val hashOperations = redisTemplate.opsForHash<String, String>()
        val existingHash = hashOperations.get(redisKey, FIELD_REQUEST_HASH) ?: return null
        if (existingHash != requestHash) {
            return IdempotencyLookup.Conflict
        }
        val status = hashOperations.get(redisKey, FIELD_STATUS)?.toIntOrNull()
        val body = hashOperations.get(redisKey, FIELD_BODY)
        if (status == null || status !in 100..599 || body == null) {
            return IdempotencyLookup.Conflict
        }
        return IdempotencyLookup.Replay(
            status = status,
            body = body,
            contentType = safeContentType(hashOperations.get(redisKey, FIELD_CONTENT_TYPE)),
        )
    }

    private fun safeContentType(value: String?): String =
        value?.takeIf { it.equals(MediaType.APPLICATION_JSON_VALUE, ignoreCase = true) }
            ?: MediaType.APPLICATION_JSON_VALUE

    companion object {
        private const val FIELD_REQUEST_HASH = "requestHash"
        private const val FIELD_STATUS = "status"
        private const val FIELD_BODY = "body"
        private const val FIELD_CONTENT_TYPE = "contentType"
        private val ACQUIRE_CLAIM_SCRIPT =
            DefaultRedisScript(
                """
                if redis.call('EXISTS', KEYS[1]) == 1 then
                    return 0
                end
                local current = tonumber(redis.call('GET', KEYS[2]) or '0')
                if current >= tonumber(ARGV[3]) then
                    return -1
                end
                local claimed = redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2], 'NX')
                if not claimed then
                    return 0
                end
                local count = redis.call('INCR', KEYS[2])
                if count == 1 then
                    redis.call('EXPIRE', KEYS[2], ARGV[4])
                end
                return 1
                """.trimIndent(),
                Long::class.java,
            )
        private val DISCARD_CLAIM_SCRIPT =
            DefaultRedisScript(
                """
                if redis.call('GET', KEYS[1]) ~= ARGV[1] then
                    return 0
                end
                redis.call('DEL', KEYS[1])
                local count = tonumber(redis.call('GET', KEYS[2]) or '0')
                if count <= 1 then
                    redis.call('DEL', KEYS[2])
                else
                    redis.call('DECR', KEYS[2])
                end
                return 1
                """.trimIndent(),
                Long::class.java,
            )
        private val STORE_RESULT_SCRIPT =
            DefaultRedisScript(
                """
                if redis.call('GET', KEYS[2]) ~= ARGV[1] then
                    return 0
                end
                redis.call('HSET', KEYS[1],
                    'requestHash', ARGV[2],
                    'status', ARGV[3],
                    'body', ARGV[4],
                    'contentType', ARGV[5])
                redis.call('EXPIRE', KEYS[1], ARGV[6])
                redis.call('DEL', KEYS[2])
                return 1
                """.trimIndent(),
                Long::class.java,
            )
    }
}

class IdempotencyClaimLostException : IllegalStateException("idempotency claim ownership was lost")

sealed interface IdempotencyLookup {
    data class New(
        val claimToken: String,
    ) : IdempotencyLookup

    data class Replay(
        val status: Int,
        val body: String,
        val contentType: String,
    ) : IdempotencyLookup

    data object Conflict : IdempotencyLookup

    data object InProgress : IdempotencyLookup

    data object CapacityExceeded : IdempotencyLookup
}
