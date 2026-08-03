package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagV2ConsentTicketMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath by lazy(::resolveMigration)
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `V26 records the consent timestamp only after serializing the owner event`() {
        val consentFunction =
            migration.substring(
                migration.indexOf("CREATE FUNCTION record_rag_v2_immutable_consent_v2"),
                migration.indexOf("CREATE FUNCTION read_rag_v2_immutable_effective_consent"),
            )

        // 대기한 REVOKE도 lock 취득 뒤에 시각을 잡아 effective read가 직렬 순서를 따른다.
        val advisoryLock = consentFunction.indexOf("pg_advisory_xact_lock")
        val recordedAt = consentFunction.indexOf("recorded_at := clock_timestamp()")

        assertThat(advisoryLock).isGreaterThanOrEqualTo(0)
        assertThat(recordedAt).isGreaterThan(advisoryLock)
        assertThat(consentFunction).doesNotContain("recorded_at timestamptz := clock_timestamp()")
    }

    private fun resolveMigration(): Path {
        val candidates =
            Files.list(migrationDirectory).use { paths ->
                paths
                    .filter {
                        it.fileName.toString().matches(
                            Regex("""V[0-9]+__s4_7d_rag_v2_consent_ticket_runtime\.sql"""),
                        )
                    }.toList()
            }
        check(candidates.size == 1) {
            "Expected one S4.7D RAG v2 consent-ticket migration; found ${candidates.size}."
        }
        return candidates.single()
    }
}
