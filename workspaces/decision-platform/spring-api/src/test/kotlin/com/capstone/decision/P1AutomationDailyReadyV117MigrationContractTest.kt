package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

/**
 * V117 은 daily_ready 가 AUTOMATION_BOOTSTRAP 시장데이터도 인정하게 완화한다.
 *
 * 완화한 것이므로 무엇을 넓혔고 무엇을 그대로 두었는지 함께 잠근다. 특히 최신성 상한
 * (as_of <= session_date + 09:20 KST) 과 status='ACCEPTED' 는 남아 있어야 한다 - 그 둘까지
 * 사라지면 오래된 데이터로 주문이 나갈 수 있다.
 */
class P1AutomationDailyReadyV117MigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath =
        migrationDirectory.resolve("V117__p1_automation_daily_ready_accepts_bootstrap.sql")
    private val migration by lazy { Files.readString(migrationPath) }

    /**
     * 주석을 뺀 실제 SQL 만 본다. 헤더 주석이 완화의 근거로 이전 조건과 GRANT 를 인용하는데,
     * 파일 전문에 doesNotContain 을 걸면 그 산문이 걸린다 - 규칙을 설명하는 문장이 규칙에
     * 걸리는 부류의 실패다. 금지 단정은 항상 코드에만 건다.
     */
    private val sql by lazy {
        migration.lines().filterNot { it.trimStart().startsWith("--") }.joinToString("\n")
    }

    @Test
    fun `V117 widens the manifest kind and keeps acceptance and freshness intact`() {
        assertThat(sql).contains(
            "CREATE OR REPLACE FUNCTION public.p1_read_automation_runtime_state_v1",
            "manifest.manifest_kind IN ('DAILY','AUTOMATION_BOOTSTRAP')",
            "manifest.status='ACCEPTED'",
            "manifest.as_of<=((claim_row.session_date+time '09:20') AT TIME ZONE 'Asia/Seoul')",
            "session_user<>'decision_automation_runtime'",
            "'dailyShardFreshComplete',daily_ready",
        )
        assertThat(sql).doesNotContain(
            "manifest.manifest_kind='DAILY'",
            "DROP FUNCTION",
            "DROP TABLE",
            "TRUNCATE",
            "GRANT",
            "ALTER TABLE",
        )
    }

    @Test
    fun `V117 only replaces the readiness reader and touches no other function`() {
        val replaced =
            migration
                .lines()
                .filter { it.startsWith("CREATE OR REPLACE FUNCTION") || it.startsWith("CREATE FUNCTION") }
        assertThat(replaced).containsExactly(
            "CREATE OR REPLACE FUNCTION public.p1_read_automation_runtime_state_v1(",
        )
    }

    @Test
    fun `V117 is the latest migration and V93 stays immutable`() {
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
        assertThat(versions.max()).isEqualTo(119)
        assertThat(versions.count { it == 117 }).isEqualTo(1)
        assertThat(migrationDirectory.resolve("V93__p1_automation_pipeline_continuity.sql")).isRegularFile()
    }
}
