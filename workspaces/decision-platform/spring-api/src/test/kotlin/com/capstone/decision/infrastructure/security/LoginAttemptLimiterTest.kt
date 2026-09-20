package com.capstone.decision.infrastructure.security

import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.time.Duration
import java.time.Instant

/**
 * 로그인 제한이 실제로 지키는 성질을 잠근다.
 *
 * 이 제한에는 테스트가 없었다. 그래서 "프로세스 메모리에 있다"는 것이 문제라는 사실이
 * 문서에만 적혀 있었고, 고쳤을 때 고쳐졌는지 확인할 방법도 없었다.
 */
class LoginAttemptLimiterTest {
    private fun limiter(store: LoginAttemptStore) =
        LoginAttemptLimiter(
            LoginAttemptLimiterProperties(scopeHmacKey = "a".repeat(32)),
            store,
        )

    private fun failUntilLocked(
        limiter: LoginAttemptLimiter,
        username: String,
        times: Int,
    ) {
        repeat(times) {
            check(limiter.tryAcquire("203.0.113.1", username)) { "$it 번째 시도가 이미 막혔다" }
            limiter.recordFailure("203.0.113.1", username)
        }
    }

    @Test
    fun `five failures lock the account and the sixth attempt is refused`() {
        val limiter = limiter(InMemoryLoginAttemptStore())

        failUntilLocked(limiter, "demo-user", 5)

        assertFalse(limiter.tryAcquire("203.0.113.1", "demo-user"))
    }

    @Test
    fun `a second instance sees the same failures because the store is shared`() {
        // 이것이 Redis 로 옮긴 이유다. 저장소가 프로세스 안에 있으면 인스턴스를 늘릴 때마다
        // 대입 한도가 인스턴스 수만큼 늘어난다 - 5회 제한이 사실상 사라진다.
        val shared = InMemoryLoginAttemptStore()
        val first = limiter(shared)
        val second = limiter(shared)

        failUntilLocked(first, "demo-user", 5)

        assertFalse(second.tryAcquire("203.0.113.1", "demo-user"))
    }

    @Test
    fun `a restart does not unlock the account while the window is open`() {
        // 재시작은 limiter 를 새로 만드는 것이다. 저장소가 밖에 있으면 이력이 살아남는다.
        val shared = InMemoryLoginAttemptStore()
        failUntilLocked(limiter(shared), "demo-user", 5)

        assertFalse(limiter(shared).tryAcquire("203.0.113.1", "demo-user"))
    }

    @Test
    fun `another account is not locked by someone else's failures`() {
        val shared = InMemoryLoginAttemptStore()
        val limiter = limiter(shared)

        failUntilLocked(limiter, "demo-user", 5)

        assertTrue(limiter.tryAcquire("203.0.113.1", "other-user"))
    }

    @Test
    fun `the window expires and the account unlocks on its own`() {
        val store = InMemoryLoginAttemptStore()
        val key = "login:v1:user:test"
        val start = Instant.parse("2026-09-20T00:00:00Z")
        repeat(5) { store.increment(key, start) }

        assertFalse(store.allowed(key, 5, start))
        assertTrue(store.allowed(key, 5, start.plus(Duration.ofMinutes(15))))
    }

    @Test
    fun `a successful login does not count against the limit`() {
        val limiter = limiter(InMemoryLoginAttemptStore())

        repeat(20) {
            check(limiter.tryAcquire("203.0.113.1", "demo-user"))
            limiter.recordSuccess("203.0.113.1", "demo-user")
        }

        assertTrue(limiter.tryAcquire("203.0.113.1", "demo-user"))
    }

    @Test
    fun `stored keys are opaque scopes rather than usernames or addresses`() {
        // 제한이 사용자 이름이나 주소를 그대로 들고 있으면, 저장소가 새는 순간 그것도 샌다.
        // 이 검사는 통합 테스트에서 리플렉션으로 남의 비공개 필드를 뒤지고 있었다 -
        // 구현을 옮기자마자 깨졌다. 저장소를 직접 쥘 수 있는 여기가 제자리다.
        val store = InMemoryLoginAttemptStore()
        val limiter = limiter(store)

        limiter.tryAcquire("198.51.100.201", "raw-probe-user")
        limiter.recordFailure("198.51.100.201", "raw-probe-user")

        val keys = store.trackedKeys()
        assertTrue(keys.any { it.startsWith("login:v1:user:") })
        assertTrue(keys.any { it.startsWith("login:v1:deployment:") })
        assertTrue(keys.none { it.contains("raw-probe-user") || it.contains("198.51.100.201") })
    }
}
