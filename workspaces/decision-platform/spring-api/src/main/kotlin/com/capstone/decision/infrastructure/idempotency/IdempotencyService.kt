package com.capstone.decision.infrastructure.idempotency

import org.springframework.data.redis.core.StringRedisTemplate
import org.springframework.stereotype.Service
import java.time.Duration

@Service
class IdempotencyService(
    private val redisTemplate: StringRedisTemplate,
    private val properties: IdempotencyProperties,
) {
    fun lookup(
        userId: String,
        idempotencyKey: String,
        requestHash: String,
    ): IdempotencyLookup {
        val redisKey = redisKey(userId, idempotencyKey)
        val hashOperations = redisTemplate.opsForHash<String, String>()
        val existingHash = hashOperations.get(redisKey, FIELD_REQUEST_HASH) ?: return IdempotencyLookup.New(redisKey)
        if (existingHash != requestHash) {
            return IdempotencyLookup.Conflict
        }
        val status = hashOperations.get(redisKey, FIELD_STATUS)?.toIntOrNull()
        val body = hashOperations.get(redisKey, FIELD_BODY)
        val contentType = hashOperations.get(redisKey, FIELD_CONTENT_TYPE)
        if (status == null || body == null) {
            return IdempotencyLookup.Conflict
        }
        return IdempotencyLookup.Replay(
            status = status,
            body = body,
            contentType = contentType ?: "application/json",
        )
    }

    fun store(
        userId: String,
        idempotencyKey: String,
        requestHash: String,
        status: Int,
        body: String,
        contentType: String,
    ) {
        val redisKey = redisKey(userId, idempotencyKey)
        redisTemplate.opsForHash<String, String>().putAll(
            redisKey,
            mapOf(
                FIELD_REQUEST_HASH to requestHash,
                FIELD_STATUS to status.toString(),
                FIELD_BODY to body,
                FIELD_CONTENT_TYPE to contentType,
            ),
        )
        redisTemplate.expire(redisKey, Duration.ofHours(properties.ttlHours))
    }

    fun redisKey(
        userId: String,
        idempotencyKey: String,
    ): String = "idempotency:$userId:$idempotencyKey"

    companion object {
        private const val FIELD_REQUEST_HASH = "requestHash"
        private const val FIELD_STATUS = "status"
        private const val FIELD_BODY = "body"
        private const val FIELD_CONTENT_TYPE = "contentType"
    }
}

sealed interface IdempotencyLookup {
    data class New(
        val redisKey: String,
    ) : IdempotencyLookup

    data class Replay(
        val status: Int,
        val body: String,
        val contentType: String,
    ) : IdempotencyLookup

    data object Conflict : IdempotencyLookup
}
