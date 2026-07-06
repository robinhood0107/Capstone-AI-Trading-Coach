package com.capstone.decision.infrastructure.idempotency

import org.springframework.data.redis.core.StringRedisTemplate
import org.springframework.stereotype.Service
import java.time.Duration

// Redis TTL 저장소로 재시도 응답을 공유해 서버 재시작/다중 인스턴스에도 같은 key를 방어한다.
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
            // key 재사용 공격이나 클라이언트 버그는 저장 응답 재사용보다 명시적 충돌이 안전하다.
            return IdempotencyLookup.Conflict
        }
        val status = hashOperations.get(redisKey, FIELD_STATUS)?.toIntOrNull()
        val body = hashOperations.get(redisKey, FIELD_BODY)
        val contentType = hashOperations.get(redisKey, FIELD_CONTENT_TYPE)
        if (status == null || body == null) {
            // 불완전한 Redis 값은 잘못된 replay보다 conflict로 막아 부작용을 보수적으로 제한한다.
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
        // hash/status/body를 한 key에 묶어 replay 판단과 응답 복원을 같은 TTL로 관리한다.
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

    // userId를 key에 포함해 서로 다른 demo 사용자 간 idempotency key 충돌을 막는다.
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

// filter가 Redis 상태를 분기할 때 sealed 타입으로 빠진 케이스를 컴파일러가 잡게 한다.
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
