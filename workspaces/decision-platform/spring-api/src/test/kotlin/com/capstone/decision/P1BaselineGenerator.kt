package com.capstone.decision

import org.flywaydb.core.Flyway
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import java.sql.DriverManager

object P1BaselineGenerator {
    private const val FINAL_VERSION = "86"
    private const val FLYWAY_PASSWORD = "flyway-test"
    private val excludedDataTables =
        setOf(
            "actor_request_capabilities",
            "admin_audit_log",
            "async_job",
            "async_job_transition_audit",
            "audit_logs",
            "brokerage_db_capability_keys",
            "dashboard_artifact_views",
            "decision_artifacts",
            "decision_idempotency_results",
            "decision_invalidations",
            "decision_traces",
            "decision_violations",
            "decisions",
            "event_outbox",
            "flyway_schema_history",
            "kafka_poison_receipt",
            "processed_event",
            "rag_answers",
            "rag_chunks",
            "rag_documents",
            "rag_v2_immutable_public_bundle_pointers",
            "rag_v2_public_corpus_state",
            "rag_sources",
            "risk_kill_switch_transitions",
            "stream_metric_snapshot",
            "users",
        )
    private val staticSeedTables =
        setOf(
            "async_event_registry",
            "principle_presets",
            "rag_embedding_policy_state",
            "rag_v2_immutable_exact30_source_allowlist",
            "rag_v2_immutable_external_exact30_source_allowlist",
            "rag_v2_immutable_oa_track_catalog",
            "risk_kill_switch",
            "trading_session_revisions",
            "trading_sessions",
        )

    @JvmStatic
    fun main(args: Array<String>) {
        require(args.size == 1) { "P1 baseline generator requires the repository root." }
        val repositoryRoot = Path.of(args.single()).toAbsolutePath().normalize()
        val image =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")
        stablePostgresContainer(image)
            .withDatabaseName("trading")
            .withUsername("decision")
            .withPassword("baseline-admin-test")
            .withInitScript("db/test-init-calendar-roles.sql")
            .use { postgres ->
                postgres.start()
                migrate(postgres)
                normalizeStaticSeedTimestamps(postgres)
                val nonEmpty = nonEmptyTables(postgres)
                val unclassified = nonEmpty - excludedDataTables - staticSeedTables
                require(unclassified.isEmpty()) {
                    "P1 baseline seed classification is incomplete: ${unclassified.sorted().joinToString(",")}"
                }
                val normalizedSchema = normalizeDump(dump(postgres, emptyList()))
                val deferredRls =
                    normalizedSchema
                        .lineSequence()
                        .filter(::isStaticSeedRlsStatement)
                        .toList()
                val schema =
                    normalizedSchema
                        .lineSequence()
                        .filterNot(::isStaticSeedRlsStatement)
                        .joinToString("\n")
                        .trim() + "\n"
                val seed = dump(postgres, staticSeedTables.sorted())
                val baseline =
                    buildString {
                        appendLine("-- Generated from pristine PostgreSQL 16 after V1 through V$FINAL_VERSION.")
                        appendLine("-- Credential, runtime, audit, outbox, RAG, and owner-private rows are excluded.")
                        append(schema)
                        if (staticSeedTables.isNotEmpty()) {
                            appendLine()
                            appendLine("-- Allowlisted static seed rows.")
                            append(normalizeDump(seed))
                        }
                        if (deferredRls.isNotEmpty()) {
                            appendLine()
                            appendLine("-- Restore the final RLS state after static seed insertion.")
                            deferredRls.forEach(::appendLine)
                        }
                    }
                val output =
                    repositoryRoot.resolve(
                        "workspaces/decision-platform/spring-api/build/p1-baseline/" +
                            "B${FINAL_VERSION}__p1_offline_demo_baseline.sql",
                    )
                Files.createDirectories(output.parent)
                Files.writeString(output, baseline, StandardCharsets.UTF_8)
                println("P1_BASELINE_GENERATED=$output")
            }
    }

    private fun migrate(postgres: PostgreSQLContainer) {
        Flyway
            .configure()
            .dataSource(postgres.jdbcUrl, "flyway", FLYWAY_PASSWORD)
            .locations("classpath:db/migration")
            .placeholders(
                mapOf(
                    "brokerageDbCapabilityTokenSha256" to
                        SpringApiIntegrationTestBase.TEST_BROKERAGE_DB_CAPABILITY_TOKEN_SHA256,
                ),
            ).javaMigrations(s21ActorTrustMigration())
            .load()
            .migrate()
    }

    private fun nonEmptyTables(postgres: PostgreSQLContainer): Set<String> =
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            val names = mutableSetOf<String>()
            connection
                .createStatement()
                .use { statement ->
                    statement
                        .executeQuery(
                            """
                            select tablename
                            from pg_tables
                            where schemaname = 'public'
                            order by tablename
                            """.trimIndent(),
                        ).use { rows ->
                            while (rows.next()) {
                                val table = rows.getString(1)
                                connection.createStatement().use { countStatement ->
                                    countStatement.executeQuery("select count(*) from public.\"$table\"").use { count ->
                                        if (count.next() && count.getLong(1) > 0) names += table
                                    }
                                }
                            }
                        }
                }
            names
        }

    private fun normalizeStaticSeedTimestamps(postgres: PostgreSQLContainer) {
        DriverManager.getConnection(postgres.jdbcUrl, "flyway", FLYWAY_PASSWORD).use { connection ->
            connection.createStatement().use { statement ->
                val fixed = "TIMESTAMPTZ '2026-08-23 00:00:00+00'"
                statement.execute("update async_event_registry set created_at=$fixed")
                statement.execute("update principle_presets set created_at=$fixed")
                statement.execute("update rag_embedding_policy_state set changed_at=$fixed")
                statement.execute("update risk_kill_switch set changed_at=$fixed")
                statement.execute("update trading_sessions set as_of=$fixed,created_at=$fixed,updated_at=$fixed")
                statement.execute("update trading_session_revisions set as_of=$fixed,created_at=$fixed")
            }
        }
    }

    private fun dump(
        postgres: PostgreSQLContainer,
        tables: List<String>,
    ): String {
        val mode = if (tables.isEmpty()) "--schema-only" else "--data-only"
        val tableArguments = tables.flatMap { listOf("--table=public.$it") }
        val command =
            listOf(
                "pg_dump",
                "--dbname=trading",
                "--username=decision",
                mode,
                "--no-comments",
                "--no-publications",
                "--no-security-labels",
                "--no-subscriptions",
                "--exclude-table=public.flyway_schema_history",
                "--column-inserts",
                "--rows-per-insert=1",
            ) + tableArguments
        val result = postgres.execInContainer(*command.toTypedArray())
        require(result.exitCode == 0) { "pg_dump failed without exposing provider or credential data" }
        return result.stdout
    }

    private fun normalizeDump(value: String): String =
        value
            .lineSequence()
            .filterNot { line ->
                line.startsWith("\\restrict ") ||
                    line.startsWith("\\unrestrict ") ||
                    line.startsWith("--")
            }.joinToString("\n")
            .trim() + "\n"

    private fun isStaticSeedRlsStatement(line: String): Boolean =
        staticSeedTables.any { table ->
            line.startsWith("ALTER TABLE ONLY public.$table ") && line.endsWith(" ROW LEVEL SECURITY;")
        }
}
