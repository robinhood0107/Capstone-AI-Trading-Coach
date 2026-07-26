package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import tools.jackson.databind.ObjectMapper
import java.nio.file.Files
import java.nio.file.Path
import kotlin.io.path.extension

// S2.4 구현 전에 외부 계약과 최소 보안 구조를 실행 가능한 실패로 먼저 잠근다.
class S24ContractFirstTest {
    private val repositoryRoot: Path =
        Path
            .of(System.getProperty("user.dir"))
            .toAbsolutePath()
            .normalize()
            .resolve("../../..")
            .normalize()

    @Test
    fun `V10 owns the singleton gate append-only invalidations and bounded projections`() {
        val migration =
            repositoryRoot.resolve(
                "workspaces/decision-platform/spring-api/src/main/resources/db/migration/" +
                    "V10__s2_4_risk_kill_switch.sql",
            )

        assertTrue(Files.isRegularFile(migration), "S2.4 requires the next-free V10 migration")
        val sql = Files.readString(migration)
        listOf(
            "CREATE TABLE risk_kill_switch",
            "CREATE TABLE risk_kill_switch_transitions",
            "CREATE TABLE decision_invalidations",
            "ALTER TABLE decision_invalidations FORCE ROW LEVEL SECURITY",
            "CREATE FUNCTION read_kill_switch_gate",
            "CREATE FUNCTION read_decision_usability",
            "CREATE FUNCTION invalidate_unused_decisions_for_kill_switch",
            "REVOKE ALL PRIVILEGES",
        ).forEach { required ->
            assertTrue(sql.contains(required), "V10 is missing $required")
        }
        assertFalse(sql.contains("DROP TABLE risk_snapshots"), "S2.4 must leave the legacy skeleton untouched")
    }

    @Test
    fun `pure Kill Switch policy types exist behind the domain boundary`() {
        listOf(
            "com.capstone.decision.domain.risk.KillSwitchState",
            "com.capstone.decision.domain.risk.KillSwitchTransitionPolicy",
            "com.capstone.decision.domain.risk.KillSwitchReasonClass",
        ).forEach { className ->
            assertNotNull(
                runCatching { Class.forName(className) }.getOrNull(),
                "$className must be implemented as a pure domain type",
            )
        }
    }

    @Test
    fun `OpenAPI exposes only the three approved S2_4 operations`() {
        val openApi =
            ObjectMapper().readTree(
                Files.readString(repositoryRoot.resolve("contracts/openapi/openapi.json")),
            )
        val paths = openApi.path("paths")

        assertTrue(paths.has("/api/v1/risk/portfolio"))
        assertTrue(paths.has("/api/v1/risk/kill-switch"))
        assertTrue(paths.path("/api/v1/risk/portfolio").has("get"))
        assertTrue(paths.path("/api/v1/risk/kill-switch").has("get"))
        assertTrue(paths.path("/api/v1/risk/kill-switch").has("post"))
        assertFalse(paths.has("/api/v1/risk/assets/{symbol}"))
    }

    @Test
    fun `sanitized Kill Switch schema exposes exactly three public fields`() {
        val schema =
            ObjectMapper().readTree(
                Files.readString(
                    repositoryRoot.resolve("contracts/schemas/s2-4-kill-switch-state.schema.json"),
                ),
            )
        val fields =
            schema
                .path("properties")
                .propertyNames()
                .asSequence()
                .toSet()

        assertEquals(setOf("active", "reasonClass", "changedAt"), fields)
        assertTrue(schema.path("additionalProperties").isBoolean && !schema.path("additionalProperties").booleanValue())
    }

    @Test
    fun `Kill Switch authority has no cache dependency and portfolio risk has one assembler`() {
        val sourceRoot =
            repositoryRoot.resolve(
                "workspaces/decision-platform/spring-api/src/main/kotlin/com/capstone/decision",
            )
        val riskSources =
            Files
                .walk(sourceRoot)
                .use { paths ->
                    paths
                        .filter { Files.isRegularFile(it) && it.extension == "kt" }
                        .filter {
                            it.toString().contains("/risk/") ||
                                it.fileName.toString().contains("KillSwitch")
                        }.map(Files::readString)
                        .toList()
                }
        val source = riskSources.joinToString("\n")

        listOf("@Cacheable", "RedisTemplate", "ConcurrentHashMap").forEach { forbidden ->
            assertFalse(source.contains(forbidden), "Kill Switch authority must not depend on $forbidden")
        }
        assertFalse(
            riskSources.any { it.contains("risk_snapshots") },
            "S2.4 portfolio risk must not read the legacy risk_snapshots table",
        )
        assertEquals(
            1,
            Regex("""class\s+MetricSnapshotAssembler\b""").findAll(source).count(),
            "MetricSnapshotAssembler must remain the single metric assembly point",
        )
    }
}
