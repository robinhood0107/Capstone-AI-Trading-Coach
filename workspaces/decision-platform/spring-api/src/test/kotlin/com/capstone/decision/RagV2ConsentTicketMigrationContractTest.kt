package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagV2ConsentTicketMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val consentTicketMigration by lazy { readMigration("s4_7d_rag_v2_consent_ticket_runtime") }
    private val legacyConsentOrderingMigration by lazy { readMigration("s4_7d_rag_v2_legacy_consent_ordering") }

    @Test
    fun `V26 records the consent timestamp only after serializing the owner event`() {
        val consentFunction =
            consentTicketMigration.substring(
                consentTicketMigration.indexOf("CREATE FUNCTION record_rag_v2_immutable_consent_v2"),
                consentTicketMigration.indexOf("CREATE FUNCTION read_rag_v2_immutable_effective_consent"),
            )

        // 대기한 REVOKE도 lock 취득 뒤에 시각을 잡아 effective read가 직렬 순서를 따른다.
        val advisoryLock = consentFunction.indexOf("pg_advisory_xact_lock")
        val recordedAt = consentFunction.indexOf("recorded_at := clock_timestamp()")

        assertThat(advisoryLock).isGreaterThanOrEqualTo(0)
        assertThat(recordedAt).isGreaterThan(advisoryLock)
        assertThat(consentFunction).doesNotContain("recorded_at timestamptz := clock_timestamp()")
    }

    @Test
    fun `applied V25 legacy consent writer is repaired by an additive migration`() {
        val consentFunction =
            legacyConsentOrderingMigration.substring(
                legacyConsentOrderingMigration.indexOf("CREATE OR REPLACE FUNCTION record_rag_v2_immutable_consent"),
                legacyConsentOrderingMigration.indexOf("ALTER FUNCTION record_rag_v2_immutable_consent"),
            )

        // 이미 적용된 V25는 바꾸지 않고 후속 migration이 같은 직렬화 순서를 강제한다.
        val advisoryLock = consentFunction.indexOf("pg_advisory_xact_lock")
        val recordedAt = consentFunction.indexOf("recorded_at := clock_timestamp()")

        assertThat(advisoryLock).isGreaterThanOrEqualTo(0)
        assertThat(recordedAt).isGreaterThan(advisoryLock)
        assertThat(consentFunction).doesNotContain("recorded_at timestamptz := clock_timestamp()")
    }

    private fun readMigration(name: String): String = Files.readString(resolveMigration(name))

    private fun resolveMigration(name: String): Path {
        val candidates =
            Files.list(migrationDirectory).use { paths ->
                paths
                    .filter {
                        it.fileName.toString().matches(
                            Regex("""V[0-9]+__${Regex.escape(name)}\.sql"""),
                        )
                    }.toList()
            }
        check(candidates.size == 1) {
            "Expected one S4.7D migration for $name; found ${candidates.size}."
        }
        return candidates.single()
    }
}
