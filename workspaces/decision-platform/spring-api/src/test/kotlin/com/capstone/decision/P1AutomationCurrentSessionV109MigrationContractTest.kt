package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class P1AutomationCurrentSessionV109MigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath = migrationDirectory.resolve("V109__p1_automation_current_session_arm.sql")
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `V109 forward repairs the applied V91 arm function`() {
        assertThat(migration).contains(
            "CREATE OR REPLACE FUNCTION public.p1_arm_automation_v2",
            "local_now:=statement_timestamp() AT TIME ZONE 'Asia/Seoul'",
            "session_date>local_now::date",
            "session_date=local_now::date AND local_now::time<time '09:30'",
            "(target_session+time '09:30') AT TIME ZONE 'Asia/Seoul'",
        )
        assertThat(migration).doesNotContain(
            "session_date>((statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date)",
        )
    }

    @Test
    fun `V109 remains before V110 and V91 remains byte stable`() {
        val versions =
            Files.list(migrationDirectory).use { paths ->
                paths
                    .filter { Files.isRegularFile(it) && it.fileName.toString().matches(Regex("V[0-9]+__.+\\.sql")) }
                    .map {
                        it.fileName
                            .toString()
                            .substringAfter('V')
                            .substringBefore("__")
                            .toInt()
                    }.toList()
            }

        assertThat(versions.count { it == 109 }).isEqualTo(1)
        assertThat(versions).contains(110)
        assertThat(migrationDirectory.resolve("V110__p1_automation_market_data.sql"))
            .isRegularFile()
        assertThat(migrationDirectory.resolve("V91__p1_variable_quantity_policy_runtime.sql"))
            .isRegularFile()
    }
}
