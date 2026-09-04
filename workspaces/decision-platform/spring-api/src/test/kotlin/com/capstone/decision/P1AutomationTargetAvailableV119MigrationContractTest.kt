package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

/**
 * V119 는 target_available 이 claim 불가능한 예약 행을 점유로 세지 않게 좁힌다.
 *
 * 왜 잠그는가. 이것은 게이트를 완화한 것이므로 안전 조건이 함께 사라지지 않았음을 코드로
 * 고정해야 한다. 남아 있어야 하는 것 둘이 특히 중요하다.
 *
 *   1. 현재 control 버전의 ARMED/CLAIMED 행은 여전히 점유로 센다. 이게 사라지면 살아 있는
 *      세션이 두 번 예약된다.
 *   2. all_ready 가 아홉 조건을 모두 AND 로 묶는다. 하나라도 빠지면 그 조건 없이 arm 된다.
 *
 * 그리고 이 파일이 readiness 리더 하나만 교체하는지도 본다 - 완화가 다른 함수로 번지면
 * 무엇이 바뀌었는지 리뷰에서 보이지 않는다.
 */
class P1AutomationTargetAvailableV119MigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath =
        migrationDirectory.resolve("V119__p1_automation_target_available_ignores_dead_schedule.sql")
    private val migration by lazy { Files.readString(migrationPath) }

    /**
     * 주석을 뺀 실제 SQL 만 본다. 헤더 주석이 완화의 근거로 이전 조건을 인용하므로 파일
     * 전문에 doesNotContain 을 걸면 그 산문이 걸린다 - 규칙을 설명하는 문장이 규칙에 걸리는
     * 부류의 실패다. 금지 단정은 항상 코드에만 건다.
     */
    private val sql by lazy {
        migration.lines().filterNot { it.trimStart().startsWith("--") }.joinToString("\n")
    }

    @Test
    fun `V119 narrows target_available to the live control version only`() {
        assertThat(sql).contains(
            "CREATE OR REPLACE FUNCTION public.p1_automation_runtime_readiness_v1",
            "schedule.schedule_state IN ('ARMED','CLAIMED')",
            "schedule.control_version=control_row.version",
            "session_user<>'decision_automation_runtime'",
        )
        assertThat(sql).doesNotContain(
            "DROP FUNCTION",
            "DROP TABLE",
            "TRUNCATE",
            "GRANT",
            "ALTER TABLE",
            "DELETE FROM",
            "UPDATE public.automation",
        )
    }

    @Test
    fun `V119 keeps every readiness condition in the all_ready conjunction`() {
        // 아홉 조건이 모두 AND 로 남아야 한다. 하나라도 빠지면 그 조건 없이 arm 된다.
        assertThat(sql).contains(
            "all_ready:=control_configured AND certification_valid AND release_source_bound",
            "AND real_team_b_ready AND principle_current AND kill_switch_inactive",
            "AND account_baseline_matches AND unresolved_state_clear AND target_available",
        )
        // 완화가 다른 게이트로 번지지 않았는지. 각 조건의 판정식이 그대로 있어야 한다.
        assertThat(sql).contains(
            "control_row.control_state='DISARMED'",
            "control_row.brokerage_mode='KIS_MOCK'",
            "gate_row.certification_status='VALID'",
            "gate_row.clean_release_binding",
            "gate_row.real_team_b_pointer_active",
            "public.current_p1_return_signal_pointer)=31",
            "principle.status='ACTIVE'",
            "SELECT NOT active FROM public.risk_kill_switch",
            "public.p1_automation_open_work_clear_v3(p_user_id,control_row.account_id)",
        )
    }

    @Test
    fun `V119 only replaces the readiness reader and touches no other function`() {
        val replaced =
            migration
                .lines()
                .filter {
                    it.startsWith("CREATE OR REPLACE FUNCTION") || it.startsWith("CREATE FUNCTION")
                }
        assertThat(replaced).containsExactly(
            "CREATE OR REPLACE FUNCTION public.p1_automation_runtime_readiness_v1(",
        )
    }

    @Test
    fun `V119 remains unique and V90 stays immutable`() {
        val versions =
            Files.list(migrationDirectory).use { paths ->
                paths
                    .filter {
                        Files.isRegularFile(it) &&
                            it.fileName.toString().matches(Regex("V[0-9]+__.+\\.sql"))
                    }.map {
                        it.fileName
                            .toString()
                            .substringAfter('V')
                            .substringBefore("__")
                            .toInt()
                    }.toList()
            }
        assertThat(versions.count { it == 119 }).isEqualTo(1)
        // roll_schedule 이 control ARMED 를 요구하는 근거 파일. 이 완화가 그것을 대체하지 않는다.
        assertThat(migrationDirectory.resolve("V90__p1_mock_automation_runtime.sql")).isRegularFile()
    }
}
