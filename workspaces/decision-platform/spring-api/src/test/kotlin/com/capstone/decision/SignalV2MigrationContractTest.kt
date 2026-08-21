package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class SignalV2MigrationContractTest {
    private val directory = Path.of("src/main/resources/db/migration")
    private val migration = directory.resolve("V72__s5_signal_v2_ingest_runtime.sql")
    private val sql by lazy { Files.readString(migration) }
    private val releaseSql by lazy {
        Files.readString(directory.resolve("V73__s5_6_model_release_signal_batch.sql"))
    }
    private val repository by lazy {
        Files.readString(
            Path.of("src/main/kotlin/com/capstone/decision/infrastructure/signal/JdbcSignalV2Repository.kt"),
        )
    }

    @Test
    fun `Signal v2 migration is the dynamic next-free forward version`() {
        val versions =
            Files.list(directory).use { paths ->
                paths
                    .map { it.fileName.toString() }
                    .filter { it.matches(Regex("V[0-9]+__.+\\.sql")) }
                    .map { it.substringAfter("V").substringBefore("__").toInt() }
                    .sorted()
                    .toList()
            }
        assertThat(versions.last()).isEqualTo(78)
        assertThat(versions.takeLast(7)).containsExactly(72, 73, 74, 75, 76, 77, 78)
    }

    @Test
    fun `Signal v2 migration keeps exact digest replay RLS and fake pointer guards`() {
        assertThat(sql).contains(
            "ALTER COLUMN as_of DROP NOT NULL",
            "signal-v2-identity-v1",
            "REPLAYED",
            "INSERTED",
            "logical identity payload conflict",
            "pg_advisory_xact_lock(hashtextextended(computed_identity, 0))",
            "FORCE ROW LEVEL SECURITY",
            "current_user <> 'flyway' OR session_user <> 'decision_app'",
            "candidate.fixture",
            "candidate.provenance_class <> 'PRODUCTION'",
            "read_production_signal_v2",
        )
        assertThat(sql.indexOf("pg_advisory_xact_lock"))
            .isLessThan(sql.indexOf("WHERE stored.logical_identity_sha256 = computed_identity"))
        assertThat(sql).doesNotContain(
            "GRANT SELECT ON TABLE public.ingested_signals TO decision_app",
            "GRANT INSERT ON TABLE public.ingested_signals TO decision_app",
            "ON CONFLICT (logical_identity_sha256) DO UPDATE",
        )
    }

    @Test
    fun `Signal v2 stale lookup uses the locked daily 0810 KST cutoff`() {
        assertThat(releaseSql).contains(
            "AT TIME ZONE 'Asia/Seoul'",
            "time '08:10'",
            "current_s5_signal_batch_clock",
        )
        assertThat(repository).contains("current_s5_signal_batch_clock")
        assertThat(repository).doesNotContain(
            "close_at <= clock_timestamp()",
            "AT TIME ZONE 'Asia/Seoul'",
        )
    }
}
