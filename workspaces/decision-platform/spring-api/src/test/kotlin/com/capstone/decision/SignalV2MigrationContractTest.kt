package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class SignalV2MigrationContractTest {
    private val directory = Path.of("src/main/resources/db/migration")
    private val migration = directory.resolve("V72__s5_signal_v2_ingest_runtime.sql")
    private val sql by lazy { Files.readString(migration) }

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
        assertThat(versions.last()).isEqualTo(72)
        assertThat(versions.takeLast(2)).containsExactly(71, 72)
    }

    @Test
    fun `Signal v2 migration keeps exact digest replay RLS and fake pointer guards`() {
        assertThat(sql).contains(
            "ALTER COLUMN as_of DROP NOT NULL",
            "signal-v2-identity-v1",
            "REPLAYED",
            "INSERTED",
            "logical identity payload conflict",
            "FORCE ROW LEVEL SECURITY",
            "current_user <> 'flyway' OR session_user <> 'decision_app'",
            "candidate.fixture",
            "candidate.provenance_class <> 'PRODUCTION'",
            "read_production_signal_v2",
        )
        assertThat(sql).doesNotContain(
            "GRANT SELECT ON TABLE public.ingested_signals TO decision_app",
            "GRANT INSERT ON TABLE public.ingested_signals TO decision_app",
            "ON CONFLICT (logical_identity_sha256) DO UPDATE",
        )
    }
}
