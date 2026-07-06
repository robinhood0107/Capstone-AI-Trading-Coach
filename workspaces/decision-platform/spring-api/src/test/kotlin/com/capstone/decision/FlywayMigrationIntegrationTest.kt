package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.dao.DataIntegrityViolationException
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import java.sql.SQLException

// pgvector/pg_trgm/Flyway 제약은 H2로 대체 검증할 수 없어 실제 PostgreSQL 컨테이너로 잠근다.
@Testcontainers
@SpringBootTest
class FlywayMigrationIntegrationTest(
    @Autowired private val jdbcTemplate: JdbcTemplate,
) : SpringApiIntegrationTestBase() {
    @Test
    fun `clean database applies V1 through V4 migrations and creates required objects`() {
        val versions = queryStrings("select version from flyway_schema_history where success order by installed_rank")
        assertEquals(listOf("1", "2", "3", "4"), versions)

        val requiredTables =
            listOf(
                "users",
                "principles",
                "principle_versions",
                "decisions",
                "orders",
                "processed_event",
                "artifact_ingest_state",
                "rag_sources",
                "rag_chunks",
                "market_calendar",
            )
        requiredTables.forEach { tableName ->
            assertTrue(tableExists(tableName), "expected table $tableName to exist")
        }

        assertEquals(1, countMarketCalendarRows("KRX", "2026-06-23", true))
        assertEquals(1, countMarketCalendarRows("KRX", "2026-01-01", false))
        assertTrue(indexExists("idx_chunks_trgm"), "expected pg_trgm index for Korean keyword search")
        assertFalse(indexDefinitionLike("rag_chunks", "%ivfflat%"), "ivfflat must wait until real embeddings are loaded")
    }

    @Test
    fun `processed event rejects duplicate event per consumer`() {
        jdbcTemplate.update(
            """
            insert into processed_event (event_id, consumer_name, processed_at)
            values ('evt-duplicate', 'risk-consumer', now())
            """.trimIndent(),
        )

        assertUniqueViolation {
            jdbcTemplate.update(
                """
                insert into processed_event (event_id, consumer_name, processed_at)
                values ('evt-duplicate', 'risk-consumer', now())
                """.trimIndent(),
            )
        }
    }

    @Test
    fun `artifact ingest state rejects duplicate run file and schema version`() {
        jdbcTemplate.update(
            """
            insert into artifact_ingest_state (run_id, file_hash, schema_version, file_name, status)
            values ('run-2026-06-23', 'hash-abc', '1.0.0', 'lstm_signals.parquet', 'VALIDATED')
            """.trimIndent(),
        )

        assertUniqueViolation {
            jdbcTemplate.update(
                """
                insert into artifact_ingest_state (run_id, file_hash, schema_version, file_name, status)
                values ('run-2026-06-23', 'hash-abc', '1.0.0', 'lstm_signals.parquet', 'VALIDATED')
                """.trimIndent(),
            )
        }
    }

    @Test
    fun `orders reject reusing the same decision id`() {
        insertOrderFixture()
        jdbcTemplate.update(
            """
            insert into orders (
                order_id, user_id, account_id, decision_id, idempotency_key,
                symbol, side, order_type, quantity, status
            )
            values (
                'ord-1', 'usr-flyway', 'paper-account-1', 'dec-flyway', 'idem-order-1',
                '005930', 'BUY', 'LIMIT', 1, 'REQUESTED'
            )
            """.trimIndent(),
        )

        assertUniqueViolation {
            jdbcTemplate.update(
                """
                insert into orders (
                    order_id, user_id, account_id, decision_id, idempotency_key,
                    symbol, side, order_type, quantity, status
                )
                values (
                    'ord-2', 'usr-flyway', 'paper-account-1', 'dec-flyway', 'idem-order-2',
                    '005930', 'BUY', 'LIMIT', 1, 'REQUESTED'
                )
                """.trimIndent(),
            )
        }
    }

    private fun insertOrderFixture() {
        jdbcTemplate.update(
            """
            insert into users (user_id, username, role, password_hash)
            values ('usr-flyway', 'flyway-user', 'USER', 'test-password-hash')
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into principles (principle_id, user_id, name, mode, status)
            values ('prn-flyway', 'usr-flyway', 'Flyway Principle', 'GUIDE', 'ACTIVE')
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into principle_versions (principle_version_id, principle_id, version, rules_json)
            values ('prv-flyway-v1', 'prn-flyway', 1, '[]'::jsonb)
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into decisions (
                decision_id, user_id, account_id, principle_version_id,
                symbol, side, decision, reason_json, created_at, valid_until
            )
            values (
                'dec-flyway', 'usr-flyway', 'paper-account-1', 'prv-flyway-v1',
                '005930', 'BUY', 'ALLOW', '{}'::jsonb, now(), now() + interval '10 minutes'
            )
            """.trimIndent(),
        )
    }

    private fun tableExists(tableName: String): Boolean =
        jdbcTemplate.queryForObject(
            """
            select exists (
                select 1
                from information_schema.tables
                where table_schema = 'public' and table_name = ?
            )
            """.trimIndent(),
            Boolean::class.java,
            tableName,
        ) ?: false

    private fun indexExists(indexName: String): Boolean =
        jdbcTemplate.queryForObject(
            "select exists (select 1 from pg_indexes where schemaname = 'public' and indexname = ?)",
            Boolean::class.java,
            indexName,
        ) ?: false

    private fun indexDefinitionLike(
        tableName: String,
        pattern: String,
    ): Boolean =
        jdbcTemplate.queryForObject(
            "select exists (select 1 from pg_indexes where schemaname = 'public' and tablename = ? and indexdef like ?)",
            Boolean::class.java,
            tableName,
            pattern,
        ) ?: false

    private fun queryStrings(sql: String): List<String> = jdbcTemplate.query(sql) { rs, _ -> rs.getString(1) }

    private fun countMarketCalendarRows(
        market: String,
        calendarDate: String,
        isTradingDay: Boolean,
    ): Int =
        jdbcTemplate.queryForObject(
            """
            select count(*)
            from market_calendar
            where market = ? and calendar_date = ?::date and is_trading_day = ?
            """.trimIndent(),
            Int::class.java,
            market,
            calendarDate,
            isTradingDay,
        ) ?: 0

    private fun assertUniqueViolation(block: () -> Unit) {
        val exception =
            org.junit.jupiter.api.assertThrows<DataIntegrityViolationException> {
                block()
            }
        val sqlException = exception.findSqlException()
        assertTrue(
            sqlException?.sqlState == "23505",
            "expected SQLState 23505 but was ${sqlException?.sqlState}: ${exception.mostSpecificCause.message}",
        )
    }

    private fun Throwable.findSqlException(): SQLException? {
        var current: Throwable? = this
        while (current != null) {
            if (current is SQLException) {
                return current
            }
            current = current.cause
        }
        return null
    }

    companion object {
        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            PostgreSQLContainer(DockerImageName.parse("pgvector/pgvector:pg16"))
                .withDatabaseName("decision")
                .withUsername("decision")
                .withPassword("decision")

        @DynamicPropertySource
        @JvmStatic
        fun postgresProperties(registry: DynamicPropertyRegistry) {
            registry.add("spring.datasource.url", postgres::getJdbcUrl)
            registry.add("spring.datasource.username", postgres::getUsername)
            registry.add("spring.datasource.password", postgres::getPassword)
        }
    }
}
