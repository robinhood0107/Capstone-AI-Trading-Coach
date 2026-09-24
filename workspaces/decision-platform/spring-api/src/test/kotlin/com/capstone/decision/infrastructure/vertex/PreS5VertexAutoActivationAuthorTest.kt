package com.capstone.decision.infrastructure.vertex

import com.capstone.decision.application.rag.RagAnswerMode
import com.capstone.decision.application.rag.RagV2VertexPreparation
import io.mockk.every
import io.mockk.mockk
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.transaction.TransactionDefinition
import org.springframework.transaction.support.AbstractPlatformTransactionManager
import org.springframework.transaction.support.DefaultTransactionStatus
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermission.OWNER_READ
import java.nio.file.attribute.PosixFilePermission.OWNER_WRITE
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset

class PreS5VertexAutoActivationAuthorTest {
    @TempDir
    lateinit var root: Path

    @Test
    fun `usage past former daily cap remains telemetry and does not block packet authoring`() {
        val jdbc = mockk<NamedParameterJdbcTemplate>()
        every { jdbc.jdbcTemplate } returns mockk<JdbcTemplate>(relaxed = true)
        val jdbcProvider = mockk<ObjectProvider<NamedParameterJdbcTemplate>>()
        every { jdbcProvider.getObject() } returns jdbc
        every {
            jdbc.queryForObject(
                "select public.count_rag_v2_immutable_vertex_usage_today(:ownerUserId)",
                any<MapSqlParameterSource>(),
                Int::class.java,
            )
        } returns 51
        val author = author(jdbcProvider)

        assertThat(author.usedToday("usr_test_owner")).isEqualTo(51)
        author.author(preparation())

        assertThat(root.resolve("control/pre-s5-vertex-activation.json")).exists()
    }

    @Test
    fun `usage measurement failure does not block packet authoring`() {
        val jdbcProvider = mockk<ObjectProvider<NamedParameterJdbcTemplate>>()
        every { jdbcProvider.getObject() } throws IllegalStateException("measurement unavailable")
        val author = author(jdbcProvider)

        assertThat(author.usedToday("usr_test_owner")).isNull()
        author.author(preparation())

        assertThat(root.resolve("control/pre-s5-vertex-activation.json")).exists()
    }

    private fun author(jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>): PreS5VertexAutoActivationAuthor {
        val policyFile = root.resolve("auto-activation-policy.json")
        Files.writeString(
            policyFile,
            """
            {
              "contractId": "pre-s5-vertex-auto-activation-policy/v1",
              "projectId": "project-test",
              "operator": "test",
              "dailyGenerateCallCap": 1,
              "inputTokenCap": 10000,
              "outputTokenCap": 1000,
              "inputByteCap": 9000,
              "costCapMicrousd": 500000,
              "inputMicrousdPerToken": 3,
              "outputMicrousdPerToken": 17,
              "serviceAccountSecurityEvidenceSha256": "${"a".repeat(64)}",
              "dataGovernanceStateEvidenceSha256": "${"b".repeat(64)}",
              "abuseMonitoringStateEvidenceSha256": "${"c".repeat(64)}",
              "modelAvailabilityEvidenceSha256": "${"d".repeat(64)}"
            }
            """.trimIndent(),
        )
        Files.setPosixFilePermissions(policyFile, setOf(OWNER_READ, OWNER_WRITE))
        return PreS5VertexAutoActivationAuthor(
            properties =
                RagV2VertexProperties(
                    modelId = "gemini-3.5-flash",
                    localRoot = root.toString(),
                    headCommit = "e".repeat(40),
                    treeDigest = "f".repeat(64),
                    ciDigest = "1".repeat(64),
                    securityDigest = "2".repeat(64),
                    autoActivationPolicyFile = policyFile.toString(),
                ),
            jdbcProvider = jdbcProvider,
            transactionManager = TestTransactionManager(),
            clock = Clock.fixed(Instant.parse("2026-09-24T00:00:00Z"), ZoneOffset.UTC),
        )
    }

    private fun preparation() =
        RagV2VertexPreparation(
            requestId = "req_vertex_1234567890",
            scopeClaimId = "rvs_${"3".repeat(32)}",
            questionFingerprintHmac = "4".repeat(64),
            answerMode = RagAnswerMode.DETAILED,
            embeddingProfileId = "voyage_context_4_1024_v1",
            consentEventId = "rce_vertex_1234567890",
            policyDigest = "5".repeat(64),
            processorSetDigest = "6".repeat(64),
            expiresAt = Instant.parse("2026-09-24T00:05:00Z"),
        )

    private class TestTransactionManager : AbstractPlatformTransactionManager() {
        override fun doGetTransaction(): Any = Any()

        override fun doBegin(
            transaction: Any,
            definition: TransactionDefinition,
        ) = Unit

        override fun doCommit(status: DefaultTransactionStatus) = Unit

        override fun doRollback(status: DefaultTransactionStatus) = Unit
    }
}
