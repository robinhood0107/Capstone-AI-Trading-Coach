package com.capstone.decision

import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityClaims
import com.capstone.decision.infrastructure.security.ActorCapabilityDeniedException
import com.capstone.decision.infrastructure.security.ActorCapabilityIssuer
import com.capstone.decision.infrastructure.security.ActorCapabilityPacketCodec
import com.capstone.decision.infrastructure.security.AuthDatabase
import com.capstone.decision.infrastructure.security.DatabaseActorCapabilityAuthority
import com.capstone.decision.infrastructure.security.DatabaseActorIdentityHandleIssuer
import com.zaxxer.hikari.HikariConfig
import com.zaxxer.hikari.HikariDataSource
import org.springframework.boot.test.context.TestConfiguration
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Primary
import org.springframework.core.env.Environment
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.time.Clock
import java.time.Instant
import java.time.temporal.ChronoUnit
import java.util.UUID

@TestConfiguration(proxyBeanMethods = false)
class TestActorCapabilityConfiguration {
    @Bean(destroyMethod = "close")
    @Primary
    fun testActorCapabilityIssuer(environment: Environment): TestActorCapabilityIssuer = TestActorCapabilityIssuer(environment)
}

class TestActorCapabilityIssuer(
    environment: Environment,
    private val clock: Clock = Clock.systemUTC(),
) : ActorCapabilityIssuer,
    AutoCloseable {
    private val keys: KeyPair = KeyPairGenerator.getInstance("Ed25519").generateKeyPair()
    private val dataSource: HikariDataSource? =
        if (environment.getProperty("app.identity.enabled", Boolean::class.java, true)) {
            HikariDataSource(
                HikariConfig().apply {
                    jdbcUrl = environment.getRequiredProperty("spring.datasource.url")
                    username = "decision_identity"
                    password = IDENTITY_PASSWORD
                    maximumPoolSize = 2
                    minimumIdle = 0
                    connectionTimeout = 1_000
                    validationTimeout = 500
                    initializationFailTimeout = -1
                    poolName = "test-actor-capability-authority"
                },
            )
        } else {
            null
        }
    private val authDataSource: HikariDataSource? =
        if (dataSource != null) {
            HikariDataSource(
                HikariConfig().apply {
                    jdbcUrl = environment.getRequiredProperty("spring.datasource.url")
                    username = "decision_auth"
                    password = AUTH_PASSWORD
                    maximumPoolSize = 2
                    minimumIdle = 0
                    connectionTimeout = 1_000
                    validationTimeout = 500
                    initializationFailTimeout = -1
                    poolName = "test-actor-identity-handle-issuer"
                },
            )
        } else {
            null
        }
    private val identityHandleIssuer: DatabaseActorIdentityHandleIssuer? =
        authDataSource?.let { DatabaseActorIdentityHandleIssuer(AuthDatabase(it)) }
    private val authority: DatabaseActorCapabilityAuthority? =
        dataSource?.let { DatabaseActorCapabilityAuthority(it, keys.private, keys.public, clock) }

    override fun issue(
        actorUserId: String,
        binding: ActorCapabilityBinding,
    ): String =
        authority?.issue(
            requireNotNull(identityHandleIssuer).issue(actorUserId, binding),
            binding,
        ) ?: offlineToken(actorUserId, binding)

    override fun close() {
        dataSource?.close()
        authDataSource?.close()
    }

    private fun offlineToken(
        actorUserId: String,
        binding: ActorCapabilityBinding,
    ): String {
        val actorRole = if (actorUserId == "usr_demo_admin") "ADMIN" else "USER"
        if (!binding.rolePolicy.accepts(actorRole)) throw ActorCapabilityDeniedException()
        val issuedAt = Instant.now(clock).truncatedTo(ChronoUnit.SECONDS)
        return ActorCapabilityPacketCodec.sign(
            ActorCapabilityClaims(
                actorUserId = actorUserId,
                actorRole = actorRole,
                actorSecurityVersion = 1,
                operation = binding.operation,
                targetKind = binding.targetKind,
                targetId = binding.targetId,
                payloadHash = binding.payloadHash,
                requestId = "req_" + compactUuid(),
                transactionId = "txn_" + compactUuid(),
                nonce = compactUuid(),
                issuedAt = issuedAt,
                expiresAt = issuedAt.plusSeconds(15),
            ),
            keys.private,
        )
    }

    private fun compactUuid(): String = UUID.randomUUID().toString().replace("-", "")

    private companion object {
        const val IDENTITY_PASSWORD = "identity-test-secret-0001"
        const val AUTH_PASSWORD = "auth-test-secret-0001"
    }
}
