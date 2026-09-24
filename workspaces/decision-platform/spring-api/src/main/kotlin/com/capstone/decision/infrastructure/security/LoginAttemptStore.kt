package com.capstone.decision.infrastructure.security

import org.slf4j.LoggerFactory
import org.springframework.context.annotation.Primary
import org.springframework.data.redis.core.StringRedisTemplate
import org.springframework.data.redis.core.script.DefaultRedisScript
import org.springframework.stereotype.Component
import java.time.Duration
import java.time.Instant

/**
 * 로그인 실패 이력을 어디에 두는지를 가른다.
 *
 * 프로세스 메모리에 두면 두 가지가 무너진다. 재시작하면 이력이 사라져 잠긴 계정이 풀리고,
 * 인스턴스를 늘리면 인스턴스마다 5회씩 허용된다 - 즉 대입 한도가 인스턴스 수만큼 늘어난다.
 */
interface LoginAttemptStore {
    /** 이 열쇠의 실패 수가 한도 미만인가. */
    fun allowed(
        key: String,
        limit: Int,
        now: Instant,
    ): Boolean

    /** 실패 하나를 적는다. 창이 지나면 스스로 사라진다. */
    fun increment(
        key: String,
        now: Instant,
    )

    /**
     * 기록한 실패를 전부 지운다.
     *
     * 운영자가 잠긴 계정을 손으로 푸는 수단이자, 테스트가 서로의 상태를 물려받지 않게
     * 하는 자리다. 이것이 없으면 테스트가 남의 비공개 필드를 리플렉션으로 뒤지게 되고,
     * 그러면 구현을 옮길 때마다 테스트가 깨진다 - 실제로 그렇게 깨졌다.
     */
    fun clear()
}

/**
 * 공유 저장소. 인스턴스가 몇 개든, 재시작을 하든 같은 창을 본다.
 *
 * Redis 가 없을 때 조용히 통과시키지 않는다. 그러면 보호가 사라진 것을 아무도 모른다.
 * 대신 프로세스 메모리로 내려가고 그 사실을 표식으로 남긴다 - 지금까지의 동작보다
 * 약해지지 않으면서, 약해진 순간이 로그에 남는다.
 *
 * 구현이 둘이므로 [Primary] 로 어느 쪽을 주입할지 못 박는다. 표시하지 않으면 기동이
 * 모호한 주입으로 실패하고, 그 실패는 컴파일이 아니라 컨테이너를 띄울 때 나온다.
 */
@Primary
@Component
class RedisLoginAttemptStore(
    private val redisTemplate: StringRedisTemplate,
    private val fallback: InMemoryLoginAttemptStore,
) : LoginAttemptStore {
    override fun allowed(
        key: String,
        limit: Int,
        now: Instant,
    ): Boolean {
        val stored =
            try {
                redisTemplate.opsForValue().get(namespaced(key))
            } catch (exception: RuntimeException) {
                degrade(exception)
                return fallback.allowed(key, limit, now)
            }
        return (stored?.toIntOrNull() ?: 0) < limit
    }

    override fun increment(
        key: String,
        now: Instant,
    ) {
        try {
            redisTemplate.execute(
                INCREMENT_SCRIPT,
                listOf(namespaced(key)),
                WINDOW.toMillis().toString(),
            )
        } catch (exception: RuntimeException) {
            degrade(exception)
            fallback.increment(key, now)
        }
    }

    override fun clear() {
        try {
            val keys = redisTemplate.keys(namespaced("*"))
            if (keys.isNotEmpty()) {
                redisTemplate.delete(keys)
            }
        } catch (exception: RuntimeException) {
            degrade(exception)
        }
        fallback.clear()
    }

    private fun degrade(exception: RuntimeException) {
        // 표식을 고정 문자열로 둔다. 로그를 사람이 읽지 않아도 검색으로 잡힌다.
        LOGGER.warn("CAPSTONE_LOGIN_LIMIT_STORE=DEGRADED_TO_PROCESS_MEMORY", exception)
    }

    private fun namespaced(key: String): String = "login-attempt:v1:$key"

    private companion object {
        val LOGGER: org.slf4j.Logger = LoggerFactory.getLogger(RedisLoginAttemptStore::class.java)
        val WINDOW: Duration = Duration.ofMinutes(15)
        val INCREMENT_SCRIPT =
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

/**
 * 종전 동작. Redis 가 닿지 않을 때의 바닥이며, 테스트가 쓰는 구현이기도 하다.
 *
 * 열쇠 수를 묶어 둔다 - 묶지 않으면 아무나 만들어 낸 사용자 이름으로 메모리를 채울 수 있다.
 */
@Component
class InMemoryLoginAttemptStore : LoginAttemptStore {
    private val attempts = LinkedHashMap<String, Attempt>(16, 0.75f, true)
    private val lock = Any()

    override fun allowed(
        key: String,
        limit: Int,
        now: Instant,
    ): Boolean =
        synchronized(lock) {
            pruneExpired(now)
            val attempt = attempts[key] ?: return@synchronized true
            if (Duration.between(attempt.startedAt, now) >= WINDOW) {
                attempts.remove(key)
                return@synchronized true
            }
            attempt.failures < limit
        }

    override fun increment(
        key: String,
        now: Instant,
    ) {
        synchronized(lock) {
            val current = attempts[key]
            attempts[key] =
                if (current == null || Duration.between(current.startedAt, now) >= WINDOW) {
                    Attempt(failures = 1, startedAt = now)
                } else {
                    current.copy(failures = current.failures + 1)
                }
            evictOverflow()
        }
    }

    override fun clear() {
        synchronized(lock) { attempts.clear() }
    }

    /** 기록한 열쇠들. 열쇠 모양이 불투명한지 확인하는 테스트가 쓴다. */
    fun trackedKeys(): Set<String> = synchronized(lock) { attempts.keys.toSet() }

    private fun pruneExpired(now: Instant) {
        val iterator = attempts.entries.iterator()
        while (iterator.hasNext()) {
            if (Duration.between(iterator.next().value.startedAt, now) >= WINDOW) {
                iterator.remove()
            }
        }
    }

    private fun evictOverflow() {
        while (attempts.size > MAX_TRACKED_KEYS) {
            val eldest = attempts.entries.iterator()
            if (!eldest.hasNext()) return
            eldest.next()
            eldest.remove()
        }
    }

    private data class Attempt(
        val failures: Int,
        val startedAt: Instant,
    )

    companion object {
        const val MAX_TRACKED_KEYS = 20_000
        val WINDOW: Duration = Duration.ofMinutes(15)
    }
}
