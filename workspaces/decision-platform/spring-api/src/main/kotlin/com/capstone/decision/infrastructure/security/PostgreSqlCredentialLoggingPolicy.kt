package com.capstone.decision.infrastructure.security

import java.sql.Connection

// bootstrap과 rotation이 credential bind 전에 같은 PostgreSQL 실효 logging policy를 fail-closed로 검증한다.
internal object PostgreSqlCredentialLoggingPolicy {
    fun requireSafe(connection: Connection) {
        connection.createStatement().use { statement ->
            statement.queryTimeout = QUERY_TIMEOUT_SECONDS
            statement
                .executeQuery(
                    """
                    select current_setting('log_parameter_max_length')::integer,
                           current_setting('log_parameter_max_length_on_error')::integer
                    """.trimIndent(),
                ).use { result ->
                    check(result.next()) { "PostgreSQL credential logging policy is unavailable." }
                    check(result.getInt(1) == 0 && result.getInt(2) == 0) {
                        "PostgreSQL credential parameter logging must be disabled."
                    }
                    check(!result.next()) { "PostgreSQL credential logging policy is ambiguous." }
                }
        }
    }

    private const val QUERY_TIMEOUT_SECONDS = 10
}
