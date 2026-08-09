package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class ForeignNewsSentimentMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath by lazy(::resolveMigration)
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `foreign-news runtime uses the dynamic next free Flyway version`() {
        val migrations = migrationFiles()
        val selectedVersion = migrationVersion(migrationPath)
        val previousHighest = migrations.filter { migrationVersion(it) < selectedVersion }.maxOf(::migrationVersion)

        assertThat(selectedVersion).isEqualTo(previousHighest + 1)
        assertThat(selectedVersion).isEqualTo(49)
    }

    @Test
    fun `foreign-news keeps owner RLS state-only lanes and definer-only writer reader capabilities`() {
        assertThat(migration).contains(
            "foreign_news_sentiment_aggregates",
            "ENABLE ROW LEVEL SECURITY",
            "FORCE ROW LEVEL SECURITY",
            "FINNHUB_PERSONAL_LOCAL",
            "SEC_OFFICIAL",
            "FED_OFFICIAL",
            "GDELT_OFFLINE_REFERENCE",
            "append_owned_foreign_news_sentiment",
            "read_owned_foreign_news_sentiment",
            "REFERENCES users(user_id) ON DELETE RESTRICT",
            "session_user <> 'decision_market_writer'",
            "session_user <> 'decision_app'",
            "SECURITY DEFINER",
            "REVOKE ALL PRIVILEGES ON TABLE foreign_news_sentiment_aggregates",
        )
        assertThat(migration).doesNotContain(
            "headline text",
            "article_body",
            "provider_response",
            "raw_response",
            "credential text",
            "GRANT SELECT ON TABLE foreign_news_sentiment_aggregates TO decision_app",
            "GRANT INSERT ON TABLE foreign_news_sentiment_aggregates TO decision_market_writer",
            "gdelt_http",
        )
    }

    private fun resolveMigration(): Path {
        val candidates =
            migrationFiles().filter {
                it.fileName.toString().matches(Regex("""V[0-9]+__pre_s5_foreign_news_sanitized_runtime\.sql"""))
            }
        check(candidates.size == 1) { "Expected one foreign-news runtime migration, found ${candidates.size}." }
        return candidates.single()
    }

    private fun migrationFiles(): List<Path> =
        Files.list(migrationDirectory).use { paths ->
            paths
                .filter { it.fileName.toString().matches(Regex("""V[0-9]+__.+\.sql""")) }
                .sorted()
                .toList()
        }

    private fun migrationVersion(path: Path): Int =
        requireNotNull(
            Regex("""^V([0-9]+)__""")
                .find(path.fileName.toString())
                ?.groupValues
                ?.get(1)
                ?.toIntOrNull(),
        ) { "Flyway migration version is missing from ${path.fileName}." }
}
