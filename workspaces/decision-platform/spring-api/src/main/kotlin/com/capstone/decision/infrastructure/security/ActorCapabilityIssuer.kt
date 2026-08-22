package com.capstone.decision.infrastructure.security

import com.zaxxer.hikari.HikariConfig
import com.zaxxer.hikari.HikariDataSource
import org.springframework.beans.factory.ObjectProvider
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component
import java.nio.charset.StandardCharsets

@ConfigurationProperties("app.identity")
data class ActorCapabilityProperties(
    val jdbcUrl: String = "",
    val username: String = "decision_identity",
    val password: String = "",
) {
    fun validate() {
        require(jdbcUrl.startsWith("jdbc:postgresql://") && !jdbcUrl.contains(Regex("[\\r\\n]"))) {
            "Actor capability issuer requires an explicit PostgreSQL JDBC URL."
        }
        require(username == "decision_identity") { "Actor capability issuer must use decision_identity." }
        require(password.toByteArray(StandardCharsets.UTF_8).size in 16..256) {
            "Actor capability issuer password must be injected from a secret store."
        }
    }
}

@Configuration
@EnableConfigurationProperties(ActorCapabilityProperties::class)
@ConditionalOnProperty(prefix = "app.identity", name = ["enabled"], havingValue = "true", matchIfMissing = true)
class ActorCapabilityConfiguration {
    @Bean(destroyMethod = "close")
    fun actorCapabilityDatabase(properties: ActorCapabilityProperties): ActorCapabilityDatabase {
        properties.validate()
        return ActorCapabilityDatabase(
            HikariDataSource(
                HikariConfig().apply {
                    jdbcUrl = properties.jdbcUrl
                    username = properties.username
                    password = properties.password
                    maximumPoolSize = 2
                    minimumIdle = 0
                    connectionTimeout = 1_000
                    validationTimeout = 500
                    initializationFailTimeout = 1_000
                    poolName = "actor-capability-pool"
                },
            ),
        )
    }
}

class ActorCapabilityDatabase(
    val dataSource: HikariDataSource,
) : AutoCloseable {
    override fun close() = dataSource.close()
}

interface ActorCapabilityIssuer {
    fun issue(actorUserId: String): String
}

@Component
class JdbcActorCapabilityIssuer(
    private val databaseProvider: ObjectProvider<ActorCapabilityDatabase>,
) : ActorCapabilityIssuer {
    override fun issue(actorUserId: String): String {
        val database =
            databaseProvider.ifAvailable
                ?: error("Actor capability database is unavailable.")
        val token =
            JdbcTemplate(database.dataSource).queryForObject(
                "SELECT issue_actor_request_capability(?)",
                String::class.java,
                actorUserId,
            )
        return token ?: throw ActorCapabilityDeniedException()
    }
}

class ActorCapabilityDeniedException : RuntimeException("Actor capability is unavailable.")
