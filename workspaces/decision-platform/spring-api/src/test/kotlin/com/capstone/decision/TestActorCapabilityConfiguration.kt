package com.capstone.decision

import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.application.security.AuthenticatedActorRef
import com.capstone.decision.infrastructure.risk.ActorScopedReadQuery
import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityClaims
import com.capstone.decision.infrastructure.security.ActorCapabilityDeniedException
import com.capstone.decision.infrastructure.security.ActorCapabilityIssuer
import com.capstone.decision.infrastructure.security.ActorCapabilityPacketCodec
import com.capstone.decision.infrastructure.security.ActorRlsScope
import com.capstone.decision.infrastructure.security.AuthDatabase
import com.capstone.decision.infrastructure.security.DatabaseActorCapabilityAuthority
import com.capstone.decision.infrastructure.security.DatabaseActorIdentityHandleIssuer
import com.zaxxer.hikari.HikariConfig
import com.zaxxer.hikari.HikariDataSource
import org.springframework.beans.factory.support.StaticListableBeanFactory
import org.springframework.boot.test.context.TestConfiguration
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Primary
import org.springframework.core.env.Environment
import org.springframework.jdbc.datasource.DriverManagerDataSource
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken
import org.springframework.security.core.context.SecurityContextHolder
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.time.Clock
import java.time.Instant
import java.time.temporal.ChronoUnit
import java.util.UUID
import javax.sql.DataSource

@TestConfiguration(proxyBeanMethods = false)
class TestActorCapabilityConfiguration {
    @Bean(destroyMethod = "close")
    @Primary
    fun testActorCapabilityIssuer(environment: Environment): TestActorCapabilityIssuer = TestActorCapabilityIssuer(environment)

    @Bean
    @Primary
    fun testActorScopedReadQuery(
        environment: Environment,
        actorRlsScope: ActorRlsScope,
    ): ActorScopedReadQuery {
        val dataSource =
            DriverManagerDataSource(
                environment.getProperty(
                    "spring.datasource.url",
                    "jdbc:postgresql://127.0.0.1:5432/decision",
                ),
                "decision_app",
                "app-test",
            )
        val provider =
            StaticListableBeanFactory(mapOf("actorScopedDataSource" to dataSource))
                .getBeanProvider(DataSource::class.java)
        return ActorScopedReadQuery(provider, actorRlsScope)
    }
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
        actor: AuthenticatedActorRef,
        binding: ActorCapabilityBinding,
    ): String =
        authority?.issue(
            requireNotNull(identityHandleIssuer).issue(actor, binding),
            binding,
        ) ?: offlineToken(actor.expectedUserId, binding)

    fun actorRef(actorUserId: String): AuthenticatedActorRef {
        val password = if (actorUserId == "usr_demo_admin") TEST_ADMIN_PASSWORD else TEST_USER_PASSWORD
        return requireNotNull(authDataSource)
            .connection
            .use { connection ->
                connection
                    .prepareStatement(
                        """
                        select session_handle,actor_user_id,actor_security_version
                        from authenticate_demo_actor_session_v1(?,?,43200)
                        """.trimIndent(),
                    ).use { statement ->
                        statement.setString(1, if (actorUserId == "usr_demo_admin") "demo-admin" else "demo-user")
                        statement.setString(2, password)
                        statement.executeQuery().use { result ->
                            check(result.next())
                            AuthenticatedActorRef(
                                result.getString("session_handle"),
                                result.getString("actor_user_id"),
                                result.getLong("actor_security_version"),
                            )
                        }
                    }
            }
    }

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
        val TEST_USER_PASSWORD = SpringApiIntegrationTestBase.TEST_USER_PASSWORD
        val TEST_ADMIN_PASSWORD = SpringApiIntegrationTestBase.TEST_ADMIN_PASSWORD
    }
}

internal fun <T> asTestActor(
    issuer: TestActorCapabilityIssuer,
    actorUserId: String = "usr_demo_user",
    block: () -> T,
): T {
    val actorRef = issuer.actorRef(actorUserId)
    return asTestActor(
        actorRef = actorRef,
        username = if (actorUserId == "usr_demo_admin") "demo-admin" else "demo-user",
        role = if (actorUserId == "usr_demo_admin") "ADMIN" else "USER",
        block = block,
    )
}

internal fun <T> asTestActor(
    actorRef: AuthenticatedActorRef,
    username: String,
    role: String,
    block: () -> T,
): T {
    val previous = SecurityContextHolder.getContext()
    val context = SecurityContextHolder.createEmptyContext()
    context.authentication =
        UsernamePasswordAuthenticationToken(
            AppPrincipal(actorRef.expectedUserId, username, role, actorRef.securityVersion, actorRef),
            null,
            emptyList(),
        )
    SecurityContextHolder.setContext(context)
    return try {
        block()
    } finally {
        SecurityContextHolder.setContext(previous)
    }
}
