package com.capstone.decision

import com.capstone.decision.infrastructure.brokerage.BrokerageProperties
import com.capstone.decision.infrastructure.decision.DecisionProperties
import com.capstone.decision.infrastructure.principle.PrincipleProperties
import com.capstone.decision.infrastructure.rag.RagGuardHistoryProperties
import com.capstone.decision.infrastructure.security.DemoAccounts
import com.capstone.decision.infrastructure.security.DemoCredentialBootstrapProperties
import com.capstone.decision.infrastructure.security.DemoCredentialBundlePolicy
import com.capstone.decision.infrastructure.security.JwtProperties
import com.capstone.decision.infrastructure.security.LoginAttemptLimiterProperties
import com.capstone.decision.infrastructure.security.SecurityConfig
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNotEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertDoesNotThrow
import org.junit.jupiter.api.assertThrows
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder
import java.security.MessageDigest
import java.util.Base64
import java.util.HexFormat

// bundle 정책은 BCrypt의 정상적인 salt 차이를 plaintext 분리 증거로 오인하지 않도록 고정한다.
class DemoCredentialBundlePolicyTest {
    private val encoder = BCryptPasswordEncoder(12)
    private val separationKey = ByteArray(32) { index -> (index + 1).toByte() }
    private val user = DemoAccounts.identities.single { it.username == "demo-user" }
    private val admin = DemoAccounts.identities.single { it.username == "demo-admin" }

    @Test
    fun `same plaintext with different bcrypt salts has one reuse tag and is rejected`() {
        val plaintext = "synthetic-shared-demo-password"
        val userBundle = prepare(user.userId, plaintext)
        val adminBundle = prepare(admin.userId, plaintext)
        val verifiedUser = DemoCredentialBundlePolicy.verify(userBundle, user, separationKey)
        val verifiedAdmin = DemoCredentialBundlePolicy.verify(adminBundle, admin, separationKey)

        assertNotEquals(verifiedUser.passwordHash, verifiedAdmin.passwordHash)
        assertTrue(MessageDigest.isEqual(verifiedUser.reuseTag, verifiedAdmin.reuseTag))
        assertThrows<IllegalArgumentException> {
            DemoCredentialBundlePolicy.requireSeparated(verifiedUser, verifiedAdmin)
        }
    }

    @Test
    fun `distinct plaintexts produce valid separated role bound bundles`() {
        val verifiedUser =
            DemoCredentialBundlePolicy.verify(
                prepare(user.userId, "synthetic-user-password"),
                user,
                separationKey,
            )
        val verifiedAdmin =
            DemoCredentialBundlePolicy.verify(
                prepare(admin.userId, "synthetic-admin-password"),
                admin,
                separationKey,
            )

        assertFalse(MessageDigest.isEqual(verifiedUser.reuseTag, verifiedAdmin.reuseTag))
        assertDoesNotThrow { DemoCredentialBundlePolicy.requireSeparated(verifiedUser, verifiedAdmin) }
    }

    @Test
    fun `bundle rejects role swap version change and independently edited evidence`() {
        val serialized = prepare(user.userId, "synthetic-user-password")
        val fields = serialized.split(":").toMutableList()
        assertTrue(fields.size == 5)

        assertThrows<IllegalArgumentException> {
            DemoCredentialBundlePolicy.verify(serialized, admin, separationKey)
        }
        assertThrows<IllegalArgumentException> {
            DemoCredentialBundlePolicy.verify(fields.copyOfChanged(0, "s21-v2"), user, separationKey)
        }
        assertThrows<IllegalArgumentException> {
            DemoCredentialBundlePolicy.verify(
                fields.copyOfChanged(2, Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(32) { 7 })),
                user,
                separationKey,
            )
        }
        assertThrows<IllegalArgumentException> {
            DemoCredentialBundlePolicy.verify(
                fields.copyOfChanged(3, requireNotNull(encoder.encode("different-valid-password"))),
                user,
                separationKey,
            )
        }
        assertThrows<IllegalArgumentException> {
            DemoCredentialBundlePolicy.verify(
                fields.copyOfChanged(4, Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(32) { 9 })),
                user,
                separationKey,
            )
        }
    }

    @Test
    fun `bundle key is exact base64url 32 bytes and distinct from other auth secrets`() {
        val encodedKey = Base64.getUrlEncoder().withoutPadding().encodeToString(separationKey)
        val userBundle = prepare(user.userId, "synthetic-user-password")
        val adminBundle = prepare(admin.userId, "synthetic-admin-password")
        val properties =
            DemoCredentialBootstrapProperties(
                userCredentialBundle = userBundle,
                adminCredentialBundle = adminBundle,
                separationKey = encodedKey,
            )
        val jwt = JwtProperties(secret = "j".repeat(32), issuer = "issuer", audience = "audience")
        val login = LoginAttemptLimiterProperties(scopeHmacKey = "l".repeat(32))
        val principle = PrincipleProperties(cursorHmacKey = "p".repeat(32))
        val decision = DecisionProperties(idempotencyScopeHmacKey = "d".repeat(32))
        val brokerageCapability = "c".repeat(32)
        val brokerage =
            BrokerageProperties(
                idempotencyScopeHmacKey = "b".repeat(32),
                databaseCapabilityToken = brokerageCapability,
                databaseCapabilityTokenSha256 =
                    HexFormat
                        .of()
                        .formatHex(
                            MessageDigest
                                .getInstance("SHA-256")
                                .digest(brokerageCapability.toByteArray()),
                        ),
            )
        val rag =
            RagGuardHistoryProperties(
                historySecretDirectory = "/tmp/synthetic-rag-history-secrets",
                idempotencyScopeHmacKey = "i".repeat(32),
                requestFingerprintHmacKey = "f".repeat(32),
                providerUsageHmacKey = "u".repeat(32),
                rateLimitHmacKey = "r".repeat(32),
                historyCursorHmacKey = "h".repeat(32),
            )

        assertDoesNotThrow {
            SecurityConfig().authSecretSeparation(
                jwt,
                login,
                properties,
                principle,
                decision,
                brokerage,
                rag,
            )
        }
        assertThrows<IllegalArgumentException> {
            DemoCredentialBundlePolicy.decodeSeparationKey(
                Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(31)),
            )
        }

        properties.separationKey =
            Base64.getUrlEncoder().withoutPadding().encodeToString(jwt.secret.toByteArray())
        assertThrows<IllegalArgumentException> {
            SecurityConfig().authSecretSeparation(jwt, login, properties, principle, decision, brokerage, rag)
        }

        properties.separationKey = encodedKey
        principle.cursorHmacKey = "p".repeat(32)
        brokerage.databaseCapabilityToken = brokerage.idempotencyScopeHmacKey
        brokerage.databaseCapabilityTokenSha256 =
            HexFormat
                .of()
                .formatHex(
                    MessageDigest
                        .getInstance("SHA-256")
                        .digest(brokerage.databaseCapabilityToken.toByteArray()),
                )
        assertThrows<IllegalArgumentException> {
            SecurityConfig().authSecretSeparation(jwt, login, properties, principle, decision, brokerage, rag)
        }

        brokerage.databaseCapabilityToken = brokerageCapability
        brokerage.databaseCapabilityTokenSha256 =
            HexFormat
                .of()
                .formatHex(
                    MessageDigest
                        .getInstance("SHA-256")
                        .digest(brokerage.databaseCapabilityToken.toByteArray()),
                )
        properties.separationKey =
            Base64.getUrlEncoder().withoutPadding().encodeToString(login.scopeHmacKey.toByteArray())
        assertThrows<IllegalArgumentException> {
            SecurityConfig().authSecretSeparation(jwt, login, properties, principle, decision, brokerage, rag)
        }

        properties.separationKey = encodedKey
        principle.cursorHmacKey = jwt.secret
        assertThrows<IllegalArgumentException> {
            SecurityConfig().authSecretSeparation(jwt, login, properties, principle, decision, brokerage, rag)
        }

        principle.cursorHmacKey = "p".repeat(32)
        rag.historyCursorHmacKey = rag.rateLimitHmacKey
        assertThrows<IllegalArgumentException> {
            SecurityConfig().authSecretSeparation(jwt, login, properties, principle, decision, brokerage, rag)
        }
    }

    @Test
    fun `preparation rejects plaintext beyond the bcrypt 72 byte equivalence boundary`() {
        assertThrows<IllegalArgumentException> {
            prepare(user.userId, "p".repeat(73))
        }
    }

    private fun prepare(
        userId: String,
        password: String,
    ): String {
        val identity = DemoAccounts.byUserId(userId) ?: error("unknown test identity")
        val chars = password.toCharArray()
        return try {
            DemoCredentialBundlePolicy.prepare(identity, chars, separationKey, encoder)
        } finally {
            chars.fill('\u0000')
        }
    }

    private fun List<String>.copyOfChanged(
        index: Int,
        value: String,
    ): String = toMutableList().also { it[index] = value }.joinToString(":")
}
