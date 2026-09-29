package com.capstone.decision.infrastructure.security

import org.springframework.dao.DuplicateKeyException
import org.springframework.security.crypto.password.PasswordEncoder
import org.springframework.stereotype.Service
import java.nio.charset.StandardCharsets
import java.security.SecureRandom
import java.time.Duration
import java.util.Base64
import java.util.Locale

@Service
class FullPasswordAccountService(
    private val accounts: FullPasswordAccountRepository,
    private val demoAccounts: DemoAccountService,
    private val passwordEncoder: PasswordEncoder,
    jwtProperties: JwtProperties,
) {
    private val sessionTtlSeconds = Math.toIntExact(Duration.ofHours(jwtProperties.ttlHours).seconds)
    private val dummyPasswordHash =
        DemoCredentialHashPolicy.requireValid(
            requireNotNull(passwordEncoder.encode(randomDummyPassword())),
        )

    fun login(
        identifier: String,
        password: String,
    ): AuthenticatedAccount? {
        if (identifier == LEGACY_DEMO_USERNAME) {
            return demoAccounts.authenticate(LEGACY_DEMO_USERNAME, password)
        }
        if (identifier == "demo-admin") {
            passwordEncoder.matches(password, dummyPasswordHash)
            return null
        }
        return authenticateEmail(identifier, password)
    }

    /** Email accounts only; the fixed demo names stay with [DemoAccountService]. */
    fun authenticateEmail(
        identifier: String,
        password: String,
    ): AuthenticatedAccount? {
        val email = normalizeEmail(identifier)
        if (email == null) {
            passwordEncoder.matches(password, dummyPasswordHash)
            return null
        }
        if (!passwordWithinLoginBoundary(password)) return null
        return accounts.authenticate(email, password, dummyPasswordHash, sessionTtlSeconds)
    }

    fun register(
        emailInput: String,
        password: String,
    ): AuthenticatedAccount {
        val email = normalizeEmail(emailInput) ?: throw IllegalArgumentException("Invalid email address.")
        validateNewPassword(password)
        val hash = passwordEncoder.encode(password) ?: error("Password encoding failed.")
        return try {
            accounts.register(email, hash, sessionTtlSeconds)
        } catch (_: DuplicateKeyException) {
            throw PasswordAccountAlreadyExistsException()
        } catch (error: org.springframework.dao.DataAccessException) {
            if (SignupCapacity.isReached(error)) throw SignupCapacityReachedException()
            throw error
        }
    }

    fun addPassword(
        userId: String,
        emailInput: String,
        password: String,
    ): AuthenticatedAccount {
        val email = normalizeEmail(emailInput) ?: throw IllegalArgumentException("Invalid email address.")
        validateNewPassword(password)
        val hash = passwordEncoder.encode(password) ?: error("Password encoding failed.")
        return try {
            accounts.addPassword(userId, email, hash, sessionTtlSeconds)
        } catch (_: DuplicateKeyException) {
            throw PasswordAccountAlreadyExistsException()
        }
    }

    /**
     * 이메일 비밀번호 계정만 바꾼다. 현재 비밀번호가 틀리면 null 을 돌려준다.
     * demo 계정은 운영자 서명 번들로만 회전하므로 [PasswordLoginNotConfiguredException]으로 거부한다.
     */
    fun changePassword(
        userId: String,
        currentPassword: String,
        newPassword: String,
    ): AuthenticatedAccount? {
        if (userId in DEMO_USER_IDS) throw PasswordLoginNotConfiguredException()
        validateNewPassword(newPassword)
        require(currentPassword != newPassword) { "New password must differ from the current password." }
        if (!passwordWithinLoginBoundary(currentPassword)) return null
        val hash = passwordEncoder.encode(newPassword) ?: error("Password encoding failed.")
        return try {
            accounts.changePassword(userId, currentPassword, hash, sessionTtlSeconds)
        } catch (error: org.springframework.dao.DataAccessException) {
            val missing =
                generateSequence<Throwable>(error) { it.cause }
                    .filterIsInstance<java.sql.SQLException>()
                    .any { it.sqlState == "P0002" }
            if (missing) throw PasswordLoginNotConfiguredException()
            throw error
        }
    }

    private fun normalizeEmail(value: String): String? {
        val normalized = value.trim().lowercase(Locale.ROOT)
        return normalized.takeIf { normalized.length <= MAX_EMAIL_LENGTH && EMAIL.matches(normalized) }
    }

    private fun validateNewPassword(password: String) {
        val bytes = password.toByteArray(StandardCharsets.UTF_8)
        try {
            require(password.length in MIN_PASSWORD_CHARACTERS..MAX_PASSWORD_CHARACTERS && bytes.size <= MAX_BCRYPT_BYTES) {
                "Password must contain 15 to 64 characters and at most 72 UTF-8 bytes."
            }
        } finally {
            bytes.fill(0)
        }
    }

    private fun passwordWithinLoginBoundary(password: String): Boolean {
        val bytes = password.toByteArray(StandardCharsets.UTF_8)
        return try {
            bytes.size in 1..MAX_BCRYPT_BYTES
        } finally {
            bytes.fill(0)
        }
    }

    private fun randomDummyPassword(): String {
        val bytes = ByteArray(32)
        SecureRandom().nextBytes(bytes)
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
    }

    private companion object {
        const val LEGACY_DEMO_USERNAME = "demo-user"
        val DEMO_USER_IDS = setOf("usr_demo_user", "usr_demo_admin")
        const val MAX_EMAIL_LENGTH = 254
        const val MIN_PASSWORD_CHARACTERS = 15
        const val MAX_PASSWORD_CHARACTERS = 64
        const val MAX_BCRYPT_BYTES = 72
        val EMAIL = Regex("^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\\.[a-z]{2,}$")
    }
}

class PasswordAccountAlreadyExistsException : RuntimeException()

class SignupCapacityReachedException : RuntimeException()

class PasswordLoginNotConfiguredException : RuntimeException()

/** V214 가입 상한 트리거는 SQLSTATE 53400으로 새 계정 하나만 거부한다. */
object SignupCapacity {
    fun isReached(error: Throwable): Boolean =
        generateSequence(error) { it.cause }
            .filterIsInstance<java.sql.SQLException>()
            .any { it.sqlState == "53400" }
}
