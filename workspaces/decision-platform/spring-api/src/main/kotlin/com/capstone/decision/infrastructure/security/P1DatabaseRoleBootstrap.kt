package com.capstone.decision.infrastructure.security

import java.sql.DriverManager
import kotlin.system.exitProcess

object P1DatabaseRoleBootstrap {
    @JvmStatic
    fun main(args: Array<String>) {
        if (args.isNotEmpty()) {
            System.err.println("p1 role bootstrap failed: unexpected_arguments")
            exitProcess(1)
        }
        try {
            bootstrap(System.getenv())
            println("p1 role bootstrap completed")
        } catch (_: Exception) {
            System.err.println("p1 role bootstrap failed: bootstrap_transaction")
            exitProcess(1)
        }
    }

    fun bootstrap(environment: Map<String, String>) {
        val host = required(environment, "POSTGRES_HOST")
        require(host in setOf("127.0.0.1", "localhost", "postgres"))
        val port = required(environment, "POSTGRES_PORT").toIntOrNull()
        require(port != null && port in 1..65_535)
        val database = required(environment, "POSTGRES_DB")
        require(Regex("^[A-Za-z_][A-Za-z0-9_]{0,62}$").matches(database))
        val adminUser = required(environment, "POSTGRES_ADMIN_USER")
        require(adminUser in setOf("postgres", "decision"))
        val postgresPassword = required(environment, "POSTGRES_PASSWORD")
        val authPassword = required(environment, "POSTGRES_AUTH_PASSWORD")
        val outboxPublisherPassword = required(environment, "POSTGRES_OUTBOX_PUBLISHER_PASSWORD")
        val poisonRecorderPassword = required(environment, "POSTGRES_POISON_RECORDER_PASSWORD")
        val automationRuntimePassword = required(environment, "POSTGRES_AUTOMATION_RUNTIME_PASSWORD")
        listOf(authPassword, outboxPublisherPassword, poisonRecorderPassword, automationRuntimePassword).forEach { password ->
            require(Regex("^[0-9a-f]{64}$").matches(password))
        }
        val jdbcUrl =
            "jdbc:postgresql://$host:$port/$database?connectTimeout=5&socketTimeout=30&tcpKeepAlive=true"

        DriverManager.getConnection(jdbcUrl, adminUser, postgresPassword).use { connection ->
            connection.autoCommit = false
            try {
                connection
                    .prepareStatement("select set_config('p1.auth_password', ?, true)")
                    .use { statement ->
                        statement.setString(1, authPassword)
                        statement.executeQuery().use { rows -> check(rows.next()) }
                    }
                connection
                    .prepareStatement("select set_config('p1.outbox_publisher_password', ?, true)")
                    .use { statement ->
                        statement.setString(1, outboxPublisherPassword)
                        statement.executeQuery().use { rows -> check(rows.next()) }
                    }
                connection
                    .prepareStatement("select set_config('p1.poison_recorder_password', ?, true)")
                    .use { statement ->
                        statement.setString(1, poisonRecorderPassword)
                        statement.executeQuery().use { rows -> check(rows.next()) }
                    }
                connection
                    .prepareStatement("select set_config('p1.automation_runtime_password', ?, true)")
                    .use { statement ->
                        statement.setString(1, automationRuntimePassword)
                        statement.executeQuery().use { rows -> check(rows.next()) }
                    }
                connection.createStatement().use { statement ->
                    statement.execute(
                        """
                        DO ${'$'}p1_auth_role${'$'}
                        DECLARE
                          role_password text := current_setting('p1.auth_password', true);
                        BEGIN
                          IF role_password !~ '^[0-9a-f]{64}${'$'}' THEN
                            RAISE EXCEPTION 'P1 auth role password boundary failed';
                          END IF;
                          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_auth') THEN
                            EXECUTE format(
                              'CREATE ROLE decision_auth LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
                              role_password
                            );
                          ELSE
                            EXECUTE format(
                              'ALTER ROLE decision_auth WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
                              role_password
                            );
                          END IF;
                          ALTER ROLE decision_auth SET log_parameter_max_length = 0;
                          ALTER ROLE decision_auth SET log_parameter_max_length_on_error = 0;
                          ALTER ROLE decision_auth SET statement_timeout = '2s';
                          ALTER ROLE decision_auth SET lock_timeout = '500ms';
                          ALTER ROLE decision_auth SET idle_in_transaction_session_timeout = '5s';
                          GRANT USAGE ON SCHEMA public TO decision_auth;
                        END
                        ${'$'}p1_auth_role${'$'};
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        DO ${'$'}p1_automation_runtime_role${'$'}
                        DECLARE
                          role_password text := current_setting('p1.automation_runtime_password', true);
                        BEGIN
                          IF role_password !~ '^[0-9a-f]{64}${'$'}' THEN
                            RAISE EXCEPTION 'P1 automation runtime role password boundary failed';
                          END IF;
                          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_automation_runtime') THEN
                            EXECUTE format(
                              'CREATE ROLE decision_automation_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
                              role_password
                            );
                          ELSE
                            EXECUTE format(
                              'ALTER ROLE decision_automation_runtime WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
                              role_password
                            );
                          END IF;
                          ALTER ROLE decision_automation_runtime SET log_parameter_max_length = 0;
                          ALTER ROLE decision_automation_runtime SET log_parameter_max_length_on_error = 0;
                          ALTER ROLE decision_automation_runtime SET statement_timeout = '5s';
                          ALTER ROLE decision_automation_runtime SET lock_timeout = '500ms';
                          ALTER ROLE decision_automation_runtime SET idle_in_transaction_session_timeout = '5s';
                          EXECUTE format(
                            'GRANT CONNECT ON DATABASE %I TO decision_automation_runtime',
                            current_database()
                          );
                          GRANT USAGE ON SCHEMA public TO decision_automation_runtime;
                        END
                        ${'$'}p1_automation_runtime_role${'$'};
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        DO ${'$'}p1_outbox_publisher_role${'$'}
                        DECLARE
                          role_password text := current_setting('p1.outbox_publisher_password', true);
                        BEGIN
                          IF role_password !~ '^[0-9a-f]{64}${'$'}' THEN
                            RAISE EXCEPTION 'P1 outbox publisher role password boundary failed';
                          END IF;
                          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_outbox_publisher') THEN
                            EXECUTE format(
                              'CREATE ROLE decision_outbox_publisher LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
                              role_password
                            );
                          ELSE
                            EXECUTE format(
                              'ALTER ROLE decision_outbox_publisher WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
                              role_password
                            );
                          END IF;
                          ALTER ROLE decision_outbox_publisher SET log_parameter_max_length = 0;
                          ALTER ROLE decision_outbox_publisher SET log_parameter_max_length_on_error = 0;
                          ALTER ROLE decision_outbox_publisher SET statement_timeout = '30s';
                          ALTER ROLE decision_outbox_publisher SET lock_timeout = '500ms';
                          ALTER ROLE decision_outbox_publisher SET idle_in_transaction_session_timeout = '30s';
                          GRANT USAGE ON SCHEMA public TO decision_outbox_publisher;
                        END
                        ${'$'}p1_outbox_publisher_role${'$'};
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        DO ${'$'}p1_poison_recorder_role${'$'}
                        DECLARE
                          role_password text := current_setting('p1.poison_recorder_password', true);
                        BEGIN
                          IF role_password !~ '^[0-9a-f]{64}${'$'}' THEN
                            RAISE EXCEPTION 'P1 poison recorder role password boundary failed';
                          END IF;
                          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_poison_recorder') THEN
                            EXECUTE format(
                              'CREATE ROLE decision_poison_recorder LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
                              role_password
                            );
                          ELSE
                            EXECUTE format(
                              'ALTER ROLE decision_poison_recorder WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
                              role_password
                            );
                          END IF;
                          ALTER ROLE decision_poison_recorder SET log_parameter_max_length = 0;
                          ALTER ROLE decision_poison_recorder SET log_parameter_max_length_on_error = 0;
                          ALTER ROLE decision_poison_recorder SET statement_timeout = '5s';
                          ALTER ROLE decision_poison_recorder SET lock_timeout = '500ms';
                          ALTER ROLE decision_poison_recorder SET idle_in_transaction_session_timeout = '5s';
                          GRANT USAGE ON SCHEMA public TO decision_poison_recorder;
                        END
                        ${'$'}p1_poison_recorder_role${'$'};
                        """.trimIndent(),
                    )
                }
                connection.commit()
            } catch (error: Exception) {
                connection.rollback()
                throw error
            }
        }
    }

    private fun required(
        environment: Map<String, String>,
        name: String,
    ): String = environment[name]?.takeIf(String::isNotBlank) ?: error("$name is required")
}
