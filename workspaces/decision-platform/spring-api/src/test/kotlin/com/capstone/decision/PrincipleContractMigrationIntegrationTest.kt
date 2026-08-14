package com.capstone.decision

import com.capstone.decision.infrastructure.principle.PrincipleRuleJsonCodec
import org.flywaydb.core.Flyway
import org.flywaydb.core.api.FlywayException
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import tools.jackson.databind.ObjectMapper
import java.nio.file.Files
import java.nio.file.Path
import java.sql.DriverManager
import java.sql.SQLException

// V8은 기존 sparse Principle을 추정 변환하지 않으므로 clean/upgrade/fail-fast를 실제 PostgreSQL로 검증한다.
@Testcontainers
@SpringBootTest
class PrincipleContractMigrationIntegrationTest(
    @Autowired private val jdbcTemplate: JdbcTemplate,
    @Autowired private val objectMapper: ObjectMapper,
    @Autowired private val principleRuleJsonCodec: PrincipleRuleJsonCodec,
) : SpringApiIntegrationTestBase() {
    @Test
    fun `clean V1 through V69 migration preserves the exact Principle schema and seed`() {
        assertEquals(
            (1..69).map(Int::toString),
            jdbcTemplate.query(
                "select version from flyway_schema_history where success order by installed_rank",
            ) { result, _ -> result.getString(1) },
        )
        assertEquals(
            setOf(
                "preset_id",
                "name_ko",
                "name_en",
                "description_ko",
                "description_en",
                "mode",
                "rules_json",
                "is_active",
                "display_order",
                "created_at",
            ),
            columnNames(postgres.jdbcUrl, "principle_presets").toSet(),
        )
        assertEquals(
            setOf(
                "principle_id",
                "user_id",
                "preset_id",
                "title",
                "mode",
                "status",
                "current_version",
                "created_at",
                "updated_at",
            ),
            columnNames(postgres.jdbcUrl, "principles").toSet(),
        )
        assertEquals(
            setOf(
                "principle_version_id",
                "principle_id",
                "version",
                "preset_id",
                "title",
                "mode",
                "status",
                "rules_json",
                "changed_fields",
                "created_by",
                "created_at",
            ),
            columnNames(postgres.jdbcUrl, "principle_versions").toSet(),
        )

        assertEquals("r", foreignKeyDeleteRule("principles", "user_id"))
        assertEquals("r", foreignKeyDeleteRule("principle_versions", "principle_id"))
        assertEquals("r", foreignKeyDeleteRule("principle_versions", "created_by"))
        assertTrue(indexExists("principles_owner_updated_idx"))
        assertTrue(indexExists("principles_owner_id_idx"))
    }

    @Test
    fun `V8 database preset seed is semantically identical to the generated catalog fixture`() {
        val repositoryRoot = findRepositoryRoot()
        val fixture =
            objectMapper.readTree(
                Files.readAllBytes(repositoryRoot.resolve("contracts/examples/principle-presets.valid.json")),
            )
        val databasePresets =
            jdbcTemplate
                .query(
                    """
                    select display_order, preset_id, name_ko, name_en, description_ko, description_en,
                           mode, rules_json::text
                    from principle_presets
                    order by display_order
                    """.trimIndent(),
                ) { result, _ ->
                    linkedMapOf(
                        "order" to result.getInt("display_order"),
                        "presetId" to result.getString("preset_id"),
                        "nameKo" to result.getString("name_ko"),
                        "nameEn" to result.getString("name_en"),
                        "descriptionKo" to result.getString("description_ko"),
                        "descriptionEn" to result.getString("description_en"),
                        "mode" to result.getString("mode"),
                        "defaultRules" to
                            objectMapper.readTree(
                                principleRuleJsonCodec.encode(
                                    principleRuleJsonCodec.decode(result.getString("rules_json")),
                                ),
                            ),
                    )
                }.let(objectMapper::writeValueAsBytes)
                .let(objectMapper::readTree)

        val presetItems = fixture.path("items")
        assertEquals(presetItems, databasePresets)
        assertEquals(
            listOf("conservative", "balanced", "aggressive"),
            presetItems.values().map { it.path("presetId").stringValue() },
        )
        assertTrue(presetItems.values().all { it.path("defaultRules").size() == 8 })
        assertEquals(
            3,
            jdbcTemplate.queryForObject(
                """
                select count(*) from principle_presets
                where not jsonb_path_exists(rules_json, '${'$'}[*].evidenceRequirement')
                """.trimIndent(),
                Int::class.java,
            ),
        )
    }

    @Test
    fun `V7 to V8 upgrade succeeds without rewriting actor trust rows`() {
        val databaseUrl = createDatabase("principle_v7_upgrade")
        flyway(databaseUrl, target = "7").migrate()
        val actorFingerprint = actorFingerprint(databaseUrl)

        flyway(databaseUrl).migrate()

        assertEquals(actorFingerprint, actorFingerprint(databaseUrl))
        assertEquals(3, scalarInt(databaseUrl, "select count(*) from principle_presets"))
        assertEquals(1, scalarInt(databaseUrl, "select count(*) from flyway_schema_history where version = '8' and success"))
    }

    @Test
    fun `V8 fails before DDL and rolls back when a sparse Principle already exists`() {
        val databaseUrl = createDatabase("principle_existing_row")
        flyway(databaseUrl, target = "7").migrate()
        execute(
            databaseUrl,
            """
            insert into principles (principle_id, user_id, name, mode, status)
            values ('legacy-principle', 'usr_demo_user', 'Legacy Principle', 'GUIDE', 'ACTIVE')
            """.trimIndent(),
        )
        val before = legacyFingerprint(databaseUrl)

        val failure = assertThrows<FlywayException> { flyway(databaseUrl).migrate() }

        assertTrue(failure.stackTraceToString().contains("S2.1 V8 precondition failed"))
        assertEquals(before, legacyFingerprint(databaseUrl))
        assertEquals(0, scalarInt(databaseUrl, "select count(*) from flyway_schema_history where version = '8'"))
        assertTrue(columnNames(databaseUrl, "principles").contains("name"))
        assertFalse(columnNames(databaseUrl, "principles").contains("title"))
    }

    @Test
    fun `V8 fails without mutation when a sparse Principle version already exists`() {
        val databaseUrl = createDatabase("principle_existing_version")
        flyway(databaseUrl, target = "7").migrate()
        execute(
            databaseUrl,
            """
            insert into principles (principle_id, user_id, name, mode, status)
            values ('legacy-principle', 'usr_demo_user', 'Legacy Principle', 'GUIDE', 'ACTIVE');
            insert into principle_versions (
              principle_version_id, principle_id, version, rules_json, created_by
            )
            values (
              'legacy-version', 'legacy-principle', 1, '[]'::jsonb, 'usr_demo_user'
            );
            """.trimIndent(),
        )
        val before = legacyFingerprint(databaseUrl)

        val failure = assertThrows<FlywayException> { flyway(databaseUrl).migrate() }

        assertTrue(failure.stackTraceToString().contains("S2.1 V8 precondition failed"))
        assertEquals(before, legacyFingerprint(databaseUrl))
        assertEquals(1, scalarInt(databaseUrl, "select count(*) from principle_versions"))
        assertEquals(0, scalarInt(databaseUrl, "select count(*) from flyway_schema_history where version = '8'"))
    }

    @Test
    fun `V8 rejects a conflicting preset identity without overwrite or delete`() {
        val databaseUrl = createDatabase("principle_preset_conflict")
        flyway(databaseUrl, target = "7").migrate()
        execute(
            databaseUrl,
            """
            insert into principle_presets (preset_id, name, mode, rules_json)
            values ('conservative', 'Conflicting preset', 'STRICT', '[]'::jsonb)
            """.trimIndent(),
        )
        val before = legacyFingerprint(databaseUrl)

        val failure = assertThrows<FlywayException> { flyway(databaseUrl).migrate() }

        assertTrue(failure.stackTraceToString().contains("S2.1 V8 preset identity conflict"))
        assertEquals(before, legacyFingerprint(databaseUrl))
        assertEquals(
            "Conflicting preset",
            scalarString(databaseUrl, "select name from principle_presets where preset_id = 'conservative'"),
        )
        assertEquals(0, scalarInt(databaseUrl, "select count(*) from flyway_schema_history where version = '8'"))
    }

    @Test
    fun `Principle audit constraint allows approved actions without breaking auth rotation audit`() {
        jdbcTemplate.update(
            """
            insert into audit_logs (
              audit_log_id, user_id, actor_role, action, target_type, target_id, payload_json
            )
            values (
              'audit-principle-approved', 'usr_demo_user', 'USER',
              'PRINCIPLE_CREATED', 'PRINCIPLE', 'prc_00000000000000000000000000000000',
              '{"principleId":"prc_00000000000000000000000000000000","newVersion":1,
                "changedFields":["presetId","title","mode","status","rules"]}'::jsonb
            )
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into audit_logs (
              audit_log_id, user_id, actor_role, action, target_type, target_id, payload_json
            )
            values (
              'audit-auth-unrelated', 'usr_demo_admin', 'ADMIN',
              'DEMO_CREDENTIAL_ROTATED', 'USER_SECURITY', 'usr_demo_user', '{}'::jsonb
            )
            """.trimIndent(),
        )

        assertThrows<org.springframework.dao.DataIntegrityViolationException> {
            jdbcTemplate.update(
                """
                insert into audit_logs (
                  audit_log_id, user_id, actor_role, action, target_type, target_id, payload_json
                )
                values (
                  'audit-principle-invalid', 'usr_demo_user', 'USER',
                  'PRINCIPLE_DELETED', 'PRINCIPLE', 'prc_00000000000000000000000000000000', '{}'::jsonb
                )
                """.trimIndent(),
            )
        }
    }

    @Test
    fun `decision app has exact Principle privileges and denied mutations fail at PostgreSQL`() {
        assertTrue(tablePrivilege("principle_presets", "SELECT"))
        assertFalse(tablePrivilege("principle_presets", "INSERT"))
        assertFalse(tablePrivilege("principle_presets", "UPDATE"))
        assertFalse(tablePrivilege("principle_presets", "DELETE"))

        assertTrue(tablePrivilege("principles", "SELECT"))
        assertTrue(tablePrivilege("principles", "INSERT"))
        assertFalse(tablePrivilege("principles", "UPDATE"))
        assertTrue(columnPrivilege("principles", "title", "UPDATE"))
        assertTrue(columnPrivilege("principles", "current_version", "UPDATE"))
        assertFalse(columnPrivilege("principles", "user_id", "UPDATE"))
        assertFalse(tablePrivilege("principles", "DELETE"))

        assertTrue(tablePrivilege("principle_versions", "SELECT"))
        assertTrue(tablePrivilege("principle_versions", "INSERT"))
        assertFalse(tablePrivilege("principle_versions", "UPDATE"))
        assertFalse(tablePrivilege("principle_versions", "DELETE"))
        assertTrue(tablePrivilege("audit_logs", "INSERT"))
        assertFalse(tablePrivilege("audit_logs", "SELECT"))
        assertFalse(tablePrivilege("audit_logs", "UPDATE"))
        assertFalse(tablePrivilege("audit_logs", "DELETE"))
        assertFalse(tablePrivilege("flyway_schema_history", "SELECT"))
        assertFalse(schemaPrivilege("public", "CREATE"))

        assertDecisionAppAllowed("select count(*) from principle_presets")
        assertDecisionAppDenied("update principle_presets set is_active = false")
        assertDecisionAppDenied("update principle_versions set title = 'forbidden'")
        assertDecisionAppDenied("delete from principle_versions")
        assertDecisionAppDenied("update audit_logs set action = 'forbidden'")
        assertDecisionAppDenied("delete from audit_logs")
        assertDecisionAppDenied("select count(*) from flyway_schema_history")
        assertDecisionAppDenied("create table forbidden_principle_table(id integer)")
    }

    private fun actorFingerprint(url: String): List<List<String>> =
        queryRows(
            url,
            """
            select user_id, username, role, status, security_version::text,
                   encode(credential_reuse_tag, 'hex'),
                   encode(credential_bundle_mac, 'hex'),
                   credential_policy_version::text
            from users
            where user_id in ('usr_demo_user', 'usr_demo_admin')
            order by user_id
            """.trimIndent(),
            8,
        )

    private fun legacyFingerprint(url: String): List<String> =
        listOf(
            columnNames(url, "principle_presets").joinToString(","),
            columnNames(url, "principles").joinToString(","),
            columnNames(url, "principle_versions").joinToString(","),
            scalarInt(url, "select count(*) from principle_presets").toString(),
            scalarInt(url, "select count(*) from principles").toString(),
            scalarInt(url, "select count(*) from principle_versions").toString(),
        )

    private fun foreignKeyDeleteRule(
        table: String,
        column: String,
    ): String =
        jdbcTemplate.queryForObject(
            """
            select constraint_type.confdeltype::text
            from pg_constraint constraint_type
            join pg_class relation on relation.oid = constraint_type.conrelid
            join pg_attribute attribute
              on attribute.attrelid = relation.oid
             and attribute.attnum = any(constraint_type.conkey)
            where relation.relname = ?
              and constraint_type.contype = 'f'
              and attribute.attname = ?
            """.trimIndent(),
            String::class.java,
            table,
            column,
        ) ?: ""

    private fun indexExists(indexName: String): Boolean =
        jdbcTemplate.queryForObject(
            "select exists (select 1 from pg_indexes where schemaname = 'public' and indexname = ?)",
            Boolean::class.java,
            indexName,
        ) ?: false

    private fun tablePrivilege(
        table: String,
        privilege: String,
    ): Boolean =
        jdbcTemplate.queryForObject(
            "select has_table_privilege('decision_app', ?, ?)",
            Boolean::class.java,
            table,
            privilege,
        ) ?: false

    private fun columnPrivilege(
        table: String,
        column: String,
        privilege: String,
    ): Boolean =
        jdbcTemplate.queryForObject(
            "select has_column_privilege('decision_app', ?, ?, ?)",
            Boolean::class.java,
            table,
            column,
            privilege,
        ) ?: false

    private fun schemaPrivilege(
        schema: String,
        privilege: String,
    ): Boolean =
        jdbcTemplate.queryForObject(
            "select has_schema_privilege('decision_app', ?, ?)",
            Boolean::class.java,
            schema,
            privilege,
        ) ?: false

    private fun assertDecisionAppAllowed(sql: String) {
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.execute("set role decision_app")
                statement.execute(sql)
            }
        }
    }

    private fun assertDecisionAppDenied(sql: String) {
        val exception =
            assertThrows<SQLException> {
                DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
                    connection.createStatement().use { statement ->
                        statement.execute("set role decision_app")
                        statement.execute(sql)
                    }
                }
            }
        assertEquals("42501", exception.sqlState)
    }

    private fun columnNames(
        url: String,
        table: String,
    ): List<String> {
        require(table in setOf("principle_presets", "principles", "principle_versions"))
        return queryRows(
            url,
            """
            select column_name
            from information_schema.columns
            where table_schema = 'public' and table_name = '$table'
            order by ordinal_position
            """.trimIndent(),
            1,
        ).map(List<String>::first)
    }

    private fun createDatabase(name: String): String {
        require(Regex("^[a-z][a-z0-9_]{0,62}$").matches(name))
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            // 식별자 bind를 지원하지 않는 CREATE DATABASE에는 테스트 내부 allowlist를 먼저 적용한다.
            connection.createStatement().use { statement -> statement.execute("create database $name") }
        }
        return postgres.jdbcUrl.substringBeforeLast('/') + "/$name"
    }

    private fun execute(
        url: String,
        sql: String,
    ) {
        DriverManager.getConnection(url, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement -> statement.execute(sql) }
        }
    }

    private fun scalarInt(
        url: String,
        sql: String,
    ): Int =
        DriverManager.getConnection(url, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.executeQuery(sql).use { result ->
                    check(result.next())
                    result.getInt(1)
                }
            }
        }

    private fun scalarString(
        url: String,
        sql: String,
    ): String =
        DriverManager.getConnection(url, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.executeQuery(sql).use { result ->
                    check(result.next())
                    result.getString(1)
                }
            }
        }

    private fun queryRows(
        url: String,
        sql: String,
        columns: Int,
    ): List<List<String>> =
        DriverManager.getConnection(url, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.executeQuery(sql).use { result ->
                    buildList {
                        while (result.next()) {
                            add((1..columns).map(result::getString))
                        }
                    }
                }
            }
        }

    private fun flyway(
        url: String,
        target: String? = null,
    ): Flyway {
        val configuration =
            Flyway
                .configure()
                .dataSource(url, postgres.username, postgres.password)
                .locations("classpath:db/migration")
                .placeholders(
                    mapOf(
                        "brokerageDbCapabilityTokenSha256" to
                            SpringApiIntegrationTestBase.TEST_BROKERAGE_DB_CAPABILITY_TOKEN_SHA256,
                    ),
                ).javaMigrations(s21ActorTrustMigration())
        target?.let(configuration::target)
        return configuration.load()
    }

    private fun findRepositoryRoot(): Path {
        var current = Path.of(System.getProperty("user.dir")).toAbsolutePath()
        while (!Files.exists(current.resolve("AGENTS.md"))) {
            current = current.parent ?: error("repository root was not found")
        }
        return current
    }

    companion object {
        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            PostgreSQLContainer(postgresImage)
                .withDatabaseName("decision")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")

        @DynamicPropertySource
        @JvmStatic
        fun postgresProperties(registry: DynamicPropertyRegistry) {
            registry.add("spring.datasource.url", postgres::getJdbcUrl)
            registry.add("spring.datasource.username", postgres::getUsername)
            registry.add("spring.datasource.password", postgres::getPassword)
            registry.add("spring.flyway.user", postgres::getUsername)
            registry.add("spring.flyway.password", postgres::getPassword)
        }
    }
}
