package com.capstone.decision.infrastructure.security

import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.stereotype.Service
import java.nio.ByteBuffer
import java.nio.charset.StandardCharsets
import java.time.Instant
import java.util.HexFormat
import java.util.Locale
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

// login scope HMAC key는 JWT/cursor key와 분리된 32-byte 이상 secret으로만 주입한다.
@ConfigurationProperties("app.login")
data class LoginAttemptLimiterProperties(
    var scopeHmacKey: String = "",
) {
    fun validate() {
        require(scopeHmacKey.toByteArray(StandardCharsets.UTF_8).size >= 32) {
            "app.login.scope-hmac-key must be at least 32 bytes."
        }
    }
}

// demo 인증도 공개 login endpoint인 만큼 단일 인스턴스에서 무제한 대입을 허용하지 않는다.
@Service
class LoginAttemptLimiter(
    private val properties: LoginAttemptLimiterProperties,
    // 실패 이력은 인스턴스 밖에 둔다. 프로세스 메모리에 두면 재시작으로 잠금이 풀리고
    // 인스턴스를 늘리면 대입 한도가 인스턴스 수만큼 늘어난다.
    private val store: LoginAttemptStore,
) {
    // 예약은 이 인스턴스의 동시성 가드다. 같은 계정에 대한 비밀번호 검증이 한 프로세스
    // 안에서 몰리는 것을 막을 뿐이므로 공유하지 않는다.
    private val reservations = mutableMapOf<String, Int>()
    private val localReservation = ThreadLocal<Reservation>()
    private val lock = Any()

    init {
        properties.validate()
    }

    fun tryAcquire(
        ignoredRemoteAddress: String,
        username: String,
    ): Boolean =
        synchronized(lock) {
            localReservation.remove()
            val now = Instant.now()
            val userKey = userKey(username)
            val deploymentKey = deploymentKey()
            if (
                !store.allowed(userKey, USER_FAILURE_LIMIT, now) ||
                !store.allowed(deploymentKey, DEPLOYMENT_FAILURE_LIMIT, now) ||
                reservationCount(userKey) >= USER_FAILURE_LIMIT ||
                reservationCount(deploymentKey) >= DEPLOYMENT_RESERVATION_LIMIT
            ) {
                return@synchronized false
            }
            // password verification concurrency는 failure history와 분리해 성공 요청이 lockout을 만들지 않는다.
            reserve(userKey)
            reserve(deploymentKey)
            localReservation.set(Reservation(userKey, deploymentKey))
            true
        }

    fun recordSuccess(
        ignoredRemoteAddress: String,
        ignoredUsername: String,
    ) {
        finish(failed = false)
    }

    fun recordFailure(
        ignoredRemoteAddress: String,
        ignoredUsername: String,
    ) {
        finish(failed = true)
    }

    fun releaseReservation() {
        finish(failed = false)
    }

    private fun finish(failed: Boolean) {
        synchronized(lock) {
            val reservation = localReservation.get() ?: return
            localReservation.remove()
            release(reservation.userKey)
            release(reservation.deploymentKey)
            if (failed) {
                val now = Instant.now()
                store.increment(reservation.userKey, now)
                store.increment(reservation.deploymentKey, now)
            }
        }
    }

    private fun reservationCount(key: String): Int = reservations[key] ?: 0

    private fun reserve(key: String) {
        reservations[key] = Math.addExact(reservationCount(key), 1)
    }

    private fun release(key: String) {
        val current = reservationCount(key)
        when {
            current <= 1 -> reservations.remove(key)
            else -> reservations[key] = current - 1
        }
    }

    private fun userKey(username: String): String = scopeKey(USER_PURPOSE, username.lowercase(Locale.ROOT))

    private fun deploymentKey(): String = scopeKey(DEPLOYMENT_PURPOSE, KEY_VERSION)

    private fun scopeKey(
        purpose: String,
        vararg values: String,
    ): String {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(properties.scopeHmacKey.toByteArray(StandardCharsets.UTF_8), "HmacSHA256"))
        mac.update("capstone-login-scope\u0000$KEY_VERSION\u0000$purpose".toByteArray(StandardCharsets.UTF_8))
        values.forEach { value ->
            val bytes = value.toByteArray(StandardCharsets.UTF_8)
            mac.update(ByteBuffer.allocate(Int.SIZE_BYTES).putInt(bytes.size).array())
            mac.update(bytes)
        }
        return "login:$KEY_VERSION:$purpose:${HexFormat.of().formatHex(mac.doFinal())}"
    }

    private data class Reservation(
        val userKey: String,
        val deploymentKey: String,
    )

    companion object {
        private const val USER_FAILURE_LIMIT = 5
        private const val DEPLOYMENT_RESERVATION_LIMIT = 50
        private const val DEPLOYMENT_FAILURE_LIMIT = InMemoryLoginAttemptStore.MAX_TRACKED_KEYS
        private const val KEY_VERSION = "v1"
        private const val USER_PURPOSE = "user"
        private const val DEPLOYMENT_PURPOSE = "deployment"
    }
}
