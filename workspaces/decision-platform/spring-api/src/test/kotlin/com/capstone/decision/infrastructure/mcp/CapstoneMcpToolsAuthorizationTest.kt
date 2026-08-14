package com.capstone.decision.infrastructure.mcp

import com.capstone.decision.application.rag.RagV2EffectiveConsent
import com.capstone.decision.application.rag.RagV2RetrievalScope
import com.capstone.decision.application.rag.RagV2RuntimeService
import com.capstone.decision.application.rag.RagV2SearchEvidenceResult
import com.capstone.decision.infrastructure.vertex.S49StrongLlmProperties
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Test
import org.springframework.security.core.authority.SimpleGrantedAuthority
import org.springframework.security.core.context.SecurityContextHolder
import org.springframework.security.oauth2.jwt.Jwt
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken
import java.time.Instant

class CapstoneMcpToolsAuthorizationTest {
    @AfterEach
    fun clearSecurityContext() = SecurityContextHolder.clearContext()

    @Test
    fun `public-only OAuth client requests a DB scope with owner retrieval disabled`() {
        authenticate("mcp:rag.public")
        val fixture = fixture()
        val result = RagV2SearchEvidenceResult(scope(), emptyList(), emptyList())
        every { fixture.ragService.searchEvidence(any(), any(), any(), false) } returns result
        val context = context(result.scope)
        every { fixture.contexts.create(any(), any(), any(), any(), any(), any(), any(), any()) } returns
            (context to "receipt")
        every { fixture.contexts.evidenceSnapshot(context) } returns emptyList()

        val response = fixture.tools.ragSearch("분산투자를 설명해 주세요.", "CONCISE", listOf("RISK"))

        assertThat(response.researchContext).isEqualTo("receipt")
        verify(exactly = 1) { fixture.ragService.searchEvidence(any(), any(), any(), false) }
        verify(exactly = 0) { fixture.ragService.effectiveConsent(any()) }
    }

    @Test
    fun `owner OAuth client must have current generation consent before retrieval starts`() {
        authenticate("mcp:rag.public", "mcp:rag.owner")
        val fixture = fixture()
        every { fixture.ragService.effectiveConsent("usr_demo_user") } returns
            RagV2EffectiveConsent(
                consentEventId = "rce_${"a".repeat(32)}",
                effective = true,
                policyDigest = "0".repeat(64),
                processorSetDigest = "1".repeat(64),
                state = "GRANTED",
            )

        assertThatThrownBy {
            fixture.tools.ragSearch("개인문서를 설명해 주세요.", "CONCISE", listOf("RISK"))
        }.isInstanceOf(IllegalArgumentException::class.java)
        verify(exactly = 0) { fixture.ragService.searchEvidence(any(), any(), any(), any()) }
    }

    private fun fixture(): Fixture {
        val ragService = mockk<RagV2RuntimeService>()
        val contexts = mockk<McpResearchContextRegistry>()
        val properties = RagWebToolProperties(enabled = true, receiptHmacKey = "h".repeat(32))
        val strong =
            S49StrongLlmProperties(
                ownerConsentPolicySha256 = "a".repeat(64),
                ownerConsentProcessorSetSha256 = "b".repeat(64),
            )
        val tools =
            CapstoneMcpTools(
                ragService,
                contexts,
                mockk(),
                mockk(),
                properties,
                mockk(),
                strong,
                mockk(),
                mockk(),
            )
        return Fixture(tools, ragService, contexts)
    }

    private fun authenticate(vararg scopes: String) {
        val jwt =
            Jwt
                .withTokenValue("fixture")
                .header("alg", "ES256")
                .subject("usr_demo_user")
                .claim("client_id", "mcp_demo_client")
                .issuedAt(Instant.parse("2026-08-14T00:00:00Z"))
                .expiresAt(Instant.parse("2026-08-14T00:15:00Z"))
                .build()
        SecurityContextHolder.getContext().authentication =
            JwtAuthenticationToken(jwt, scopes.map { SimpleGrantedAuthority("SCOPE_$it") })
    }

    private fun scope() =
        RagV2RetrievalScope(
            scopeClaimId = "rvs_${"a".repeat(32)}",
            exact30GenerationId = "rgr_${"b".repeat(32)}",
            oa112GenerationId = "rgr_${"c".repeat(32)}",
            ownerGenerationId = null,
            embeddingProfileId = "voyage_context_4_1024_v1",
            policyVersion = 1,
        )

    private fun context(scope: RagV2RetrievalScope) =
        McpResearchContext(
            id = "s49_ctx_${"d".repeat(32)}",
            ownerUserId = "usr_demo_user",
            oauthClientId = "mcp_demo_client",
            question = "question",
            answerMode = "CONCISE",
            requestId = "req_mcp_public_scope_0001",
            retrievalScope = scope,
            retrievalCitations = emptyList(),
            retrievalEvidence = emptyList(),
            evidence = mutableListOf(),
            searchableUrls = mutableSetOf(),
            expiresAt = Instant.parse("2026-08-14T00:15:00Z"),
        )

    private data class Fixture(
        val tools: CapstoneMcpTools,
        val ragService: RagV2RuntimeService,
        val contexts: McpResearchContextRegistry,
    )
}
