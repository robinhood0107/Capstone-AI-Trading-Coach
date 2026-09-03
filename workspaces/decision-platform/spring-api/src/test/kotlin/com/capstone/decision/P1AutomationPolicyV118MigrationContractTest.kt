package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

/**
 * V118 은 v3 정책의 CUSTOM 조합을 막던 낡은 v1 profile CHECK 를 제거한다.
 *
 * 제거이므로 무엇을 지웠고 무엇이 남는지 함께 잠근다. 남는 것은 V111 의
 * automation_policy_versions_v3_shape_check 와 risk_profile 열거 제약이다. 그 둘까지
 * 사라지면 risk_profile 이 stop_loss/take_profit 과 무관한 값이 될 수 있다.
 */
class P1AutomationPolicyV118MigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath =
        migrationDirectory.resolve("V118__p1_automation_policy_drop_stale_v1_profile_check.sql")
    private val migration by lazy { Files.readString(migrationPath) }

    /**
     * 주석을 뺀 실제 SQL 만 본다. 헤더 주석이 제거 근거로 옛 제약과 profile 함수를 인용하는데
     * 파일 전문에 doesNotContain 을 걸면 그 산문이 걸린다.
     */
    private val sql by lazy {
        migration.lines().filterNot { it.trimStart().startsWith("--") }.joinToString("\n")
    }

    @Test
    fun `V118 drops only the stale v1 profile check`() {
        assertThat(sql).contains(
            "ALTER TABLE public.automation_policy_versions",
            "DROP CONSTRAINT IF EXISTS automation_policy_versions_check1",
        )
        assertThat(sql).doesNotContain(
            "DROP CONSTRAINT IF EXISTS automation_policy_versions_v3_shape_check",
            "DROP CONSTRAINT IF EXISTS automation_policy_versions_risk_profile_check",
            "DROP TABLE",
            "TRUNCATE",
            "DELETE",
            "UPDATE",
            "ADD CONSTRAINT",
        )
    }

    @Test
    fun `V111 still guards both policy shapes so dropping the stale check loses nothing`() {
        val v111 =
            Files.readString(
                migrationDirectory.resolve("V111__p1_automation_exit_policy_atr.sql"),
            )
        // v1 형태(atr 전부 NULL)는 여전히 profile_v1 을 요구한다 - 지운 제약과 같은 조건이다.
        assertThat(v111).contains(
            "ADD CONSTRAINT automation_policy_versions_v3_shape_check",
            "risk_profile=public.p1_automation_policy_profile_v1(stop_loss_bps,take_profit_bps)",
            "risk_profile=public.p1_automation_policy_profile_v2(",
        )
    }

    @Test
    fun `V118 is declared exactly once and V91 stays immutable`() {
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
        assertThat(versions.count { it == 118 }).isEqualTo(1)
        assertThat(migrationDirectory.resolve("V91__p1_variable_quantity_policy_runtime.sql"))
            .isRegularFile()
    }
}
