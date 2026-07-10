package com.capstone.decision.infrastructure.security

import org.springframework.stereotype.Service
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Duration
import java.time.Instant
import java.util.HexFormat

// demo 인증도 공개 login endpoint인 만큼 단일 인스턴스에서 무제한 대입을 허용하지 않는다.
@Service
class LoginAttemptLimiter {
    private val attempts = LinkedHashMap<String, Attempt>(16, 0.75f, true)
    private val lock = Any()

    fun tryAcquire(
        remoteAddress: String,
        username: String,
    ): Boolean =
        synchronized(lock) {
            val now = Instant.now()
            pruneExpired(now)
            val userKey = userKey(remoteAddress, username)
            val ipKey = ipKey(remoteAddress)
            if (!allowed(userKey, USER_FAILURE_LIMIT, now) || !allowed(ipKey, IP_FAILURE_LIMIT, now)) {
                return@synchronized false
            }
            // check와 reservation을 같은 lock 안에서 수행해 병렬 첫 burst도 제한을 넘지 못하게 한다.
            increment(userKey, now)
            increment(ipKey, now)
            evictOverflow()
            true
        }

    fun recordSuccess(
        remoteAddress: String,
        username: String,
    ) {
        synchronized(lock) {
            attempts.remove(userKey(remoteAddress, username))
        }
    }

    private fun allowed(
        key: String,
        limit: Int,
        now: Instant,
    ): Boolean {
        val attempt = attempts[key] ?: return true
        if (Duration.between(attempt.startedAt, now) >= WINDOW) {
            attempts.remove(key)
            return true
        }
        return attempt.failures < limit
    }

    private fun increment(
        key: String,
        now: Instant,
    ) {
        val current = attempts[key]
        attempts[key] =
            if (current == null || Duration.between(current.startedAt, now) >= WINDOW) {
                Attempt(failures = 1, startedAt = now)
            } else {
                current.copy(failures = current.failures + 1)
            }
    }

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

    private fun userKey(
        remoteAddress: String,
        username: String,
    ): String = "user:${digest("$remoteAddress\u0000${username.lowercase()}")}"

    private fun ipKey(remoteAddress: String): String = "ip:${digest(remoteAddress)}"

    private fun digest(value: String): String =
        HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.toByteArray(StandardCharsets.UTF_8)))

    private data class Attempt(
        val failures: Int,
        val startedAt: Instant,
    )

    companion object {
        private const val USER_FAILURE_LIMIT = 5
        private const val IP_FAILURE_LIMIT = 50
        private const val MAX_TRACKED_KEYS = 20_000
        private val WINDOW: Duration = Duration.ofMinutes(15)
    }
}
