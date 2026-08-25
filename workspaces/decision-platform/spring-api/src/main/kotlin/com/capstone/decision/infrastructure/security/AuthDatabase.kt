package com.capstone.decision.infrastructure.security

import com.zaxxer.hikari.HikariConfig
import com.zaxxer.hikari.HikariDataSource
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.context.annotation.Lazy
import java.nio.charset.StandardCharsets

@ConfigurationProperties("app.auth")
data class AuthDatabaseProperties(
    val jdbcUrl: String = "",
    val username: String = "decision_auth",
    val password: String = "",
) {
    fun validate() {
        require(jdbcUrl.startsWith("jdbc:postgresql://") && !jdbcUrl.contains(Regex("[\\r\\n]"))) {
            "Authentication database requires an explicit PostgreSQL JDBC URL."
        }
        require(username == "decision_auth") { "Authentication database must use decision_auth." }
        require(password.toByteArray(StandardCharsets.UTF_8).size in 16..256) {
            "Authentication database password must be injected from a secret store."
        }
    }
}

@Configuration
@EnableConfigurationProperties(AuthDatabaseProperties::class)
@ConditionalOnProperty(prefix = "app.identity", name = ["enabled"], havingValue = "true", matchIfMissing = true)
class AuthDatabaseConfiguration {
    @Bean(destroyMethod = "close")
    @Lazy
    fun authDatabase(properties: AuthDatabaseProperties): AuthDatabase {
        properties.validate()
        return AuthDatabase(
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
                    poolName = "authentication-pool"
                },
            ),
        )
    }
}

class AuthDatabase(
    val dataSource: HikariDataSource,
) : AutoCloseable {
    override fun close() = dataSource.close()
}
