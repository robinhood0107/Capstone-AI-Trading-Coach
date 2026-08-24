package com.capstone.decision.infrastructure.vertex

import com.capstone.decision.application.rag.RagAnswerMode
import com.capstone.decision.application.rag.RagV2EffectiveConsent
import com.capstone.decision.application.rag.RagV2RetrievalScope
import com.capstone.decision.application.rag.RagV2VertexEvidence
import com.capstone.decision.application.rag.RagV2VertexGenerationCommand
import com.capstone.decision.application.rag.RagV2VertexQuestionFingerprintPort
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.RowMapper
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.transaction.TransactionDefinition
import org.springframework.transaction.support.AbstractPlatformTransactionManager
import org.springframework.transaction.support.DefaultTransactionStatus
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneOffset

class JdbcPreS5VertexUsageLedgerTest {
    @Test
    fun `reservation binds packet expiry as PostgreSQL compatible UTC offset timestamp`() {
        val jdbc = mockk<NamedParameterJdbcTemplate>()
        val provider = mockk<ObjectProvider<NamedParameterJdbcTemplate>>()
        val fingerprintPort = mockk<RagV2VertexQuestionFingerprintPort>()
        val parameters = slot<Map<String, *>>()
        val fingerprint = "3".repeat(64)
        val expiresAt = Instant.parse("2026-08-14T01:02:03.456789Z")
        val activation = activation(expiresAt, fingerprint)
        val expectedUsageEventId = usageEventId(activation, fingerprint)

        every { provider.getIfAvailable() } returns jdbc
        every { fingerprintPort.fingerprint(any(), any()) } returns fingerprint
        every {
            jdbc.queryForObject(
                match { it.contains("set_config") },
                any<Map<String, *>>(),
                String::class.java,
            )
        } returns "usr_demo_user"
        every {
            jdbc.query(
                match { it.contains("reserve_rag_v2_immutable_vertex_usage") },
                capture(parameters),
                any<RowMapper<Pair<String, Instant>>>(),
            )
        } returns listOf(expectedUsageEventId to expiresAt)

        JdbcPreS5VertexUsageLedger(provider, TestTransactionManager(), fingerprintPort)
            .reserve(command(), activation)

        assertThat(parameters.captured["expiresAt"])
            .isEqualTo(OffsetDateTime.ofInstant(expiresAt, ZoneOffset.UTC))
            .isNotInstanceOf(Instant::class.java)
    }

    private fun command() =
        RagV2VertexGenerationCommand(
            ownerUserId = "usr_demo_user",
            requestId = "req_vertex_1234567890",
            question = "분산투자 근거를 설명해 주세요.",
            answerMode = RagAnswerMode.DETAILED,
            topics = listOf("RISK"),
            scope =
                RagV2RetrievalScope(
                    scopeClaimId = "rvs_${"a".repeat(32)}",
                    exact30GenerationId = "rgr_${"b".repeat(32)}",
                    oa112GenerationId = "rgr_${"c".repeat(32)}",
                    ownerGenerationId = null,
                    embeddingProfileId = "voyage_context_4_1024_v1",
                    policyVersion = 1,
                ),
            consent =
                RagV2EffectiveConsent(
                    consentEventId = "rce_vertex_1234567890",
                    effective = true,
                    policyDigest = "4".repeat(64),
                    processorSetDigest = "5".repeat(64),
                    state = "GRANTED",
                ),
            evidence =
                listOf(
                    RagV2VertexEvidence(
                        ordinal = 1,
                        citationId = "cit_1",
                        chunkRevisionId = "rag_v2_chk_${"d".repeat(32)}",
                        canonicalText = "검증 가능한 근거입니다.",
                        canonicalTextSha256 = "6".repeat(64),
                    ),
                ),
        )

    private fun activation(
        expiresAt: Instant,
        fingerprint: String,
    ) = PreS5VertexActivation(
        packetSha256 = "1".repeat(64),
        nonceSha256 = "2".repeat(64),
        authenticationMode = "SERVICE_ACCOUNT_OAUTH",
        projectId = "project-test-123",
        modelId = "gemini-3.5-flash",
        requestId = "req_vertex_1234567890",
        scopeClaimId = "rvs_${"a".repeat(32)}",
        questionFingerprintHmac = fingerprint,
        answerMode = "DETAILED",
        consentEventId = "rce_vertex_1234567890",
        policySha256 = "4".repeat(64),
        processorSetSha256 = "5".repeat(64),
        expiresAt = expiresAt,
        inputTokenCap = 60_512,
        outputTokenCap = 1_000,
        inputByteCap = 60_000,
        costCapMicrousd = 131_000,
        inputMicrousdPerToken = 2,
        outputMicrousdPerToken = 9,
        tokenPhysicalCallCap = 1,
        generateContentPhysicalCallCap = 1,
    )

    private fun usageEventId(
        activation: PreS5VertexActivation,
        fingerprint: String,
    ): String {
        val seed =
            listOf(
                "rag-v2-vertex-usage-event/v1",
                activation.packetSha256,
                activation.nonceSha256,
                fingerprint,
            ).joinToString("\u0000").toByteArray(StandardCharsets.UTF_8)
        return "rgr_vgu_" +
            MessageDigest
                .getInstance("SHA-256")
                .digest(seed)
                .joinToString("") { "%02x".format(java.util.Locale.ROOT, it) }
                .take(32)
    }

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
