package com.capstone.decision.infrastructure.vertex

import com.capstone.decision.application.rag.RagAnswerMode
import com.capstone.decision.application.rag.RagGenerationStatus
import com.capstone.decision.application.rag.RagV2EffectiveConsent
import com.capstone.decision.application.rag.RagV2RetrievalScope
import com.capstone.decision.application.rag.RagV2VertexEvidence
import com.capstone.decision.application.rag.RagV2VertexGenerationCommand
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import tools.jackson.databind.json.JsonMapper
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Instant

class VertexGemini35FlashGenerationAdapterTest {
    @Test
    fun `single legacy generator accepts grounded structured output without tools retries or raw artifacts`() {
        val activationReader = mockk<PreS5VertexActivationReader>()
        val oauthProvider = mockk<PreS5VertexServiceAccountOAuthProvider>()
        val ledger = mockk<JdbcPreS5VertexUsageLedger>(relaxed = true)
        val request = slot<PreS5VertexHttpRequest>()
        val http = mockk<PreS5VertexHttpExecutor>()
        val activation = activation()
        val lease = lease("a")
        val tokenAttempt = PreS5VertexTokenAttempt(lease)
        val generateContentAttempt = PreS5VertexGenerateContentAttempt(lease)
        var sentBody = ByteArray(0)
        every { activationReader.read() } returns activation
        every { ledger.reserve(any(), activation) } returns lease
        every { ledger.claimTokenAttempt(lease) } returns tokenAttempt
        every { oauthProvider.acquire(activation, tokenAttempt) } returns
            PreS5VertexAccessToken("project-test-123", "ya29.vertex-token-test".toByteArray(StandardCharsets.US_ASCII))
        every { ledger.claimGenerateContentAttempt(lease) } returns generateContentAttempt
        every { http.execute(capture(request)) } answers {
            sentBody = request.captured.body.copyOf()
            successResponse()
        }

        val result = adapter(activationReader, oauthProvider, ledger, http).generate(command())

        assertThat(result.generationStatus).isEqualTo(RagGenerationStatus.ANSWERED)
        assertThat(result.answer).isEqualTo("The reference explains the model assumption.")
        assertThat(result.citationIds).containsExactly("cit_1")
        assertThat(request.captured.endpoint.toString())
            .isEqualTo(
                "https://aiplatform.googleapis.com/v1/projects/project-test-123/locations/global/publishers/google/models/gemini-3.5-flash:generateContent",
            )
        val body = sentBody.toString(StandardCharsets.UTF_8)
        assertThat(body).contains("generationConfig", "candidateCount", "maxOutputTokens")
        val payload = JsonMapper.builder().build().readTree(body)
        assertThat(payload.properties().map { it.key })
            .containsExactly("contents", "generationConfig")
        assertThat(payload["generationConfig"].properties().map { it.key })
            .containsExactly("candidateCount", "temperature", "maxOutputTokens", "responseMimeType", "responseSchema")
        assertThat(payload["generationConfig"]["responseMimeType"].stringValue()).isEqualTo("application/json")
        val responseSchema = payload["generationConfig"]["responseSchema"]
        assertThat(responseSchema["properties"]["answer"].get("enum")).isNull()
        assertThat(responseSchema["properties"]["sentences"]["maxItems"].intValue()).isEqualTo(24)
        val sentenceSchema = responseSchema["properties"]["sentences"]["items"]["properties"]
        assertThat(sentenceSchema["text"].get("enum")).isNull()
        assertThat(sentenceSchema["citationIds"]["items"].get("enum")).isNull()
        assertThat(sentenceSchema["numericSpans"]["maxItems"].intValue()).isEqualTo(64)
        val required = payload["generationConfig"]["responseSchema"]["required"]
        assertThat((0 until required.size()).map { required[it].stringValue() })
            .containsExactly("basis", "answer", "sentences", "warnings")
        verify(exactly = 1) { ledger.reserve(any(), activation) }
        verify(exactly = 1) { ledger.claimTokenAttempt(lease) }
        verify(exactly = 1) { oauthProvider.acquire(activation, tokenAttempt) }
        verify(exactly = 1) { ledger.claimGenerateContentAttempt(lease) }
        verify(exactly = 1) { ledger.commit(lease, any()) }
        verify(exactly = 0) { ledger.markUnknownBilling(any()) }
    }

    @Test
    fun `mismatched consent fails before OAuth or provider attempt is created`() {
        val activationReader = mockk<PreS5VertexActivationReader>()
        val oauthProvider = mockk<PreS5VertexServiceAccountOAuthProvider>(relaxed = true)
        val ledger = mockk<JdbcPreS5VertexUsageLedger>(relaxed = true)
        val http = mockk<PreS5VertexHttpExecutor>(relaxed = true)
        every { activationReader.read() } returns activation()
        val command = command().copy(consent = command().consent.copy(processorSetDigest = "c".repeat(64)))

        val result = adapter(activationReader, oauthProvider, ledger, http).generate(command)

        assertThat(result.generationStatus).isEqualTo(RagGenerationStatus.GENERATION_UNAVAILABLE)
        verify(exactly = 0) { oauthProvider.acquire(any(), any()) }
        verify(exactly = 0) { ledger.reserve(any(), any()) }
        verify(exactly = 0) { http.execute(any()) }
    }

    @Test
    fun `malformed generated JSON records sanitized known usage but never returns or persists an answer`() {
        val activationReader = mockk<PreS5VertexActivationReader>()
        val oauthProvider = mockk<PreS5VertexServiceAccountOAuthProvider>()
        val ledger = mockk<JdbcPreS5VertexUsageLedger>(relaxed = true)
        val http = mockk<PreS5VertexHttpExecutor>()
        val activation = activation()
        val lease = lease("b")
        val tokenAttempt = PreS5VertexTokenAttempt(lease)
        val generateContentAttempt = PreS5VertexGenerateContentAttempt(lease)
        every { activationReader.read() } returns activation
        every { ledger.reserve(any(), activation) } returns lease
        every { ledger.claimTokenAttempt(lease) } returns tokenAttempt
        every { oauthProvider.acquire(activation, tokenAttempt) } returns
            PreS5VertexAccessToken("project-test-123", "ya29.vertex-token-test".toByteArray(StandardCharsets.US_ASCII))
        every { ledger.claimGenerateContentAttempt(lease) } returns generateContentAttempt
        every { http.execute(any()) } returns successResponse(generatedJson = "{not-json")

        val result = adapter(activationReader, oauthProvider, ledger, http).generate(command())

        assertThat(result.generationStatus).isEqualTo(RagGenerationStatus.GENERATION_UNAVAILABLE)
        assertThat(result.answer).isNull()
        verify(exactly = 1) { ledger.commit(lease, any()) }
        verify(exactly = 0) { ledger.markUnknownBilling(any()) }
    }

    @Test
    fun `Gemini 3 thought signature and hidden thought usage remain content free and valid`() {
        val activationReader = mockk<PreS5VertexActivationReader>()
        val oauthProvider = mockk<PreS5VertexServiceAccountOAuthProvider>()
        val ledger = mockk<JdbcPreS5VertexUsageLedger>(relaxed = true)
        val http = mockk<PreS5VertexHttpExecutor>()
        val activation = activation()
        val lease = lease("c")
        val tokenAttempt = PreS5VertexTokenAttempt(lease)
        val generateContentAttempt = PreS5VertexGenerateContentAttempt(lease)
        every { activationReader.read() } returns activation
        every { ledger.reserve(any(), activation) } returns lease
        every { ledger.claimTokenAttempt(lease) } returns tokenAttempt
        every { oauthProvider.acquire(activation, tokenAttempt) } returns
            PreS5VertexAccessToken("project-test-123", "ya29.vertex-token-test".toByteArray(StandardCharsets.US_ASCII))
        every { ledger.claimGenerateContentAttempt(lease) } returns generateContentAttempt
        every { http.execute(any()) } returns successResponse(includeThoughtMetadata = true)

        val result = adapter(activationReader, oauthProvider, ledger, http).generate(command())

        assertThat(result.generationStatus).isEqualTo(RagGenerationStatus.ANSWERED)
        verify(exactly = 1) { ledger.commit(lease, match { it.totalTokenCount == 23 }) }
        verify(exactly = 0) { ledger.markUnknownBilling(any()) }
    }

    private fun adapter(
        activationReader: PreS5VertexActivationReader,
        oauthProvider: PreS5VertexServiceAccountOAuthProvider,
        ledger: JdbcPreS5VertexUsageLedger,
        http: PreS5VertexHttpExecutor,
    ): VertexGemini35FlashGenerationAdapter =
        VertexGemini35FlashGenerationAdapter(
            properties =
                RagV2VertexProperties(
                    enabled = true,
                    localRoot = "/tmp/capstone-rag-control",
                    headCommit = "1".repeat(40),
                    treeDigest = "2".repeat(64),
                    ciDigest = "3".repeat(64),
                    securityDigest = "4".repeat(64),
                ),
            activationReader = activationReader,
            oauthProvider = oauthProvider,
            usageLedger = ledger,
            httpExecutor = http,
        )

    private fun command(): RagV2VertexGenerationCommand =
        RagV2VertexGenerationCommand(
            ownerUserId = "usr_demo_user",
            requestId = "req_vertex_transport_0000001",
            question = "Explain the referenced model assumption.",
            answerMode = RagAnswerMode.CONCISE,
            scope =
                RagV2RetrievalScope(
                    scopeClaimId = "rvs_${"a".repeat(32)}",
                    exact30GenerationId = "rgr_${"1".repeat(32)}",
                    oa112GenerationId = "rgr_${"2".repeat(32)}",
                    ownerGenerationId = null,
                    embeddingProfileId = "bge_m3_local_1024_v1",
                    policyVersion = 1,
                ),
            consent =
                RagV2EffectiveConsent(
                    consentEventId = "rce_${"a".repeat(32)}",
                    effective = true,
                    policyDigest = "a".repeat(64),
                    processorSetDigest = "b".repeat(64),
                    state = "GRANTED",
                ),
            evidence =
                listOf(
                    RagV2VertexEvidence(
                        ordinal = 1,
                        citationId = "cit_1",
                        chunkRevisionId = "rag_v2_chk_${"a".repeat(32)}",
                        canonicalText = "The reference explains the model assumption.",
                        canonicalTextSha256 = sha256("The reference explains the model assumption."),
                    ),
                ),
        )

    private fun activation(): PreS5VertexActivation =
        PreS5VertexActivation(
            packetSha256 = "d".repeat(64),
            nonceSha256 = "e".repeat(64),
            authenticationMode = "SERVICE_ACCOUNT_OAUTH",
            projectId = "project-test-123",
            modelId = "gemini-3.5-flash",
            requestId = "req_vertex_transport_0000001",
            scopeClaimId = "rvs_${"a".repeat(32)}",
            questionFingerprintHmac = "f".repeat(64),
            answerMode = "CONCISE",
            consentEventId = "rce_${"a".repeat(32)}",
            policySha256 = "a".repeat(64),
            processorSetSha256 = "b".repeat(64),
            expiresAt = Instant.parse("2026-08-03T12:05:00Z"),
            inputTokenCap = 13_000,
            outputTokenCap = 200,
            inputByteCap = 12_000,
            costCapMicrousd = 200_000,
            inputMicrousdPerToken = 10,
            outputMicrousdPerToken = 20,
            tokenPhysicalCallCap = 1,
            generateContentPhysicalCallCap = 1,
        )

    private fun lease(hex: String): PreS5VertexUsageLease =
        PreS5VertexUsageLease(
            usageEventId = "rgr_vgu_${hex.repeat(32)}",
            ownerUserId = "usr_demo_user",
            expiresAt = Instant.parse("2026-08-03T12:05:00Z"),
        )

    private fun sha256(value: String): String =
        MessageDigest
            .getInstance("SHA-256")
            .digest(value.toByteArray(StandardCharsets.UTF_8))
            .joinToString("") { "%02x".format(java.util.Locale.ROOT, it) }

    private fun successResponse(
        generatedJson: String = validGeneratedJson(),
        includeThoughtMetadata: Boolean = false,
    ): PreS5VertexHttpResponse {
        val escaped = generatedJson.replace("\\", "\\\\").replace("\"", "\\\"")
        val thoughtSignature = if (includeThoughtMetadata) ",\"thoughtSignature\":\"opaque-signature\"" else ""
        val thoughtUsage = if (includeThoughtMetadata) ",\"thoughtsTokenCount\":5" else ""
        val totalTokens = if (includeThoughtMetadata) 23 else 18
        return PreS5VertexHttpResponse(
            statusCode = 200,
            body =
                """
                {
                  "candidates":[{"content":{"role":"model","parts":[{"text":"$escaped"$thoughtSignature}]}}],
                  "usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":8,"totalTokenCount":$totalTokens$thoughtUsage}
                }
                """.trimIndent().toByteArray(StandardCharsets.UTF_8),
        )
    }

    private fun validGeneratedJson(): String =
        """
        {"basis":"EVIDENCE","answer":"The reference explains the model assumption.","sentences":[{"text":"The reference explains the model assumption.","citationIds":["cit_1"],"evidenceSpans":[{"citationId":"cit_1","quote":"The reference explains the model assumption."}],"numericSpans":[]}],"warnings":["SINGLE_SOURCE"]}
        """.trimIndent()
}
