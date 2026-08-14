package com.capstone.decision.infrastructure.vertex

import com.capstone.decision.application.rag.RagAnswerMode
import com.capstone.decision.application.rag.RagGenerationStatus
import com.capstone.decision.application.rag.RagV2EffectiveConsent
import com.capstone.decision.application.rag.RagV2RetrievalScope
import com.capstone.decision.application.rag.RagV2VertexEvidence
import com.capstone.decision.application.rag.RagV2VertexGenerationCommand
import com.capstone.decision.application.rag.StrongLlmAnswerBasis
import com.capstone.decision.infrastructure.mcp.BoundedWebDocument
import com.capstone.decision.infrastructure.mcp.PublicWebReaderPort
import com.capstone.decision.infrastructure.mcp.PublicWebSearchPort
import com.capstone.decision.infrastructure.mcp.RagWebToolProperties
import com.capstone.decision.infrastructure.mcp.S49WebEvidenceMetadataPort
import com.capstone.decision.infrastructure.mcp.SearxngSearchResult
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.net.URI
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Instant

class S49VertexStrongLlmGenerationAdapterTest {
    @Test
    fun `첫 evidence가 무관해도 뒤 근거를 골라 생성한다`() {
        val transport = QueueHttpClient(providerText(answerJson("분산 효과는 공분산과 관련됩니다.", "cit_2", "공분산과 관련")))
        val ledger = RecordingUsageLedger()
        val adapter = adapter(transport, ledger)

        val result = adapter.generate(command(evidence("날씨 설명입니다."), evidence("분산 효과는 공분산과 관련됩니다.")))

        assertThat(result.generationStatus).isEqualTo(RagGenerationStatus.ANSWERED)
        assertThat(result.citationIds).containsExactly("cit_2")
        assertThat(result.answer).isEqualTo("분산 효과는 공분산과 관련됩니다.")
        assertThat(ledger.committed?.toolRounds).isZero()
        assertThat(transport.calls).isEqualTo(1)
    }

    @Test
    fun `Gemini search와 read function call 뒤 web evidence를 검증한다`() {
        val url = "https://example.com/research"
        val transport =
            QueueHttpClient(
                providerFunction("capstone_web_search", "{\"query\":\"portfolio covariance\"}"),
                providerFunction("capstone_web_read", "{\"url\":\"$url\"}"),
                providerText(answerJson("상관관계가 낮으면 분산 위험이 줄 수 있습니다.", "cit_3", "상관관계가 낮으면")),
            )
        val ledger = RecordingUsageLedger()
        val adapter =
            adapter(
                transport,
                ledger,
                search = PublicWebSearchPort { listOf(SearxngSearchResult("Research", url, "summary")) },
                read = PublicWebReaderPort { BoundedWebDocument(url, "Research", "상관관계가 낮으면 분산 위험이 줄 수 있습니다.", "text/html") },
            )

        val result = adapter.generate(command(evidence("기존 근거 하나"), evidence("기존 근거 둘")))

        assertThat(result.generationStatus).isEqualTo(RagGenerationStatus.ANSWERED)
        assertThat(result.citationIds).containsExactly("cit_3")
        assertThat(transport.calls).isEqualTo(3)
        assertThat(ledger.committed).extracting("toolRounds", "searchCalls", "readCalls").containsExactly(2, 1, 1)
    }

    @Test
    fun `허용되지 않은 function은 뒤 provider 호출 없이 종료한다`() {
        val transport = QueueHttpClient(providerFunction("browser_click", "{}"))
        val ledger = RecordingUsageLedger()
        val result = adapter(transport, ledger).generate(command(evidence("근거")))

        assertThat(result.generationStatus).isEqualTo(RagGenerationStatus.GENERATION_UNAVAILABLE)
        assertThat(transport.calls).isEqualTo(1)
        assertThat(ledger.unknown).isTrue()
    }

    @Test
    fun `일반 교육 답변은 citation 없이 model knowledge로 반환한다`() {
        val transport =
            QueueHttpClient(
                providerText(
                    """{"basis":"MODEL_KNOWLEDGE","answer":"분산투자는 서로 다른 위험 요인을 함께 구성하는 개념입니다.","sentences":[{"text":"분산투자는 서로 다른 위험 요인을 함께 구성하는 개념입니다.","citationIds":[],"evidenceSpans":[],"numericSpans":[]}],"warnings":[]}""",
                ),
            )
        val ledger = RecordingUsageLedger()

        val result = adapter(transport, ledger).generate(command(evidence("무관한 현재 자료")))

        assertThat(result.generationStatus).isEqualTo(RagGenerationStatus.ANSWERED)
        assertThat(result.answerBasis).isEqualTo(StrongLlmAnswerBasis.MODEL_KNOWLEDGE)
        assertThat(result.citationIds).isEmpty()
        assertThat(result.citationCoverage).isZero()
    }

    @Test
    fun `근거 부족 응답은 재생성 없이 retrieval only로 종료한다`() {
        val transport =
            QueueHttpClient(
                providerText(
                    """{"basis":"INSUFFICIENT_EVIDENCE","answer":null,"sentences":[],"warnings":["LOW_RELEVANCE"]}""",
                ),
            )
        val ledger = RecordingUsageLedger()

        val result = adapter(transport, ledger).generate(command(evidence("무관한 자료")))

        assertThat(result.generationStatus).isEqualTo(RagGenerationStatus.RETRIEVAL_ONLY)
        assertThat(result.answerBasis).isEqualTo(StrongLlmAnswerBasis.INSUFFICIENT_EVIDENCE)
        assertThat(transport.calls).isEqualTo(1)
    }

    private fun adapter(
        transport: QueueHttpClient,
        ledger: RecordingUsageLedger,
        search: PublicWebSearchPort = PublicWebSearchPort { emptyList() },
        read: PublicWebReaderPort = PublicWebReaderPort { throw AssertionError("read not expected") },
    ) = S49VertexStrongLlmGenerationAdapter(
        S49StrongLlmProperties(
            enabled = true,
            localRoot = "/tmp",
            ownerConsentPolicySha256 = "0".repeat(64),
            ownerConsentProcessorSetSha256 = "1".repeat(64),
        ),
        RagWebToolProperties(enabled = true, receiptHmacKey = "m".repeat(32)),
        S49VertexAccessTokenProvider {
            S49VertexAccessToken("capstone-project", "access-token-value".toByteArray(), Instant.now().plusSeconds(300))
        },
        transport,
        search,
        read,
        ledger,
        S49WebEvidenceMetadataPort { _, _, _, _, _, _ -> },
    )

    private fun command(vararg evidence: RagV2VertexEvidence) =
        RagV2VertexGenerationCommand(
            ownerUserId = "usr_demo_user",
            requestId = "req_s49_abcdefghijkl",
            question = "분산투자의 위험 감소 원리를 설명해 주세요.",
            answerMode = RagAnswerMode.DETAILED,
            scope = RagV2RetrievalScope("scope", "exact", "oa", null, "voyage_context_4_1024_v1", 1),
            consent =
                RagV2EffectiveConsent(
                    consentEventId = "rce_abcdefghijkl",
                    effective = true,
                    policyDigest = "0".repeat(64),
                    processorSetDigest = "1".repeat(64),
                    state = "GRANTED",
                ),
            evidence = evidence.toList().mapIndexed { index, value -> value.copy(index + 1, "cit_${index + 1}") },
        )

    private fun evidence(text: String): RagV2VertexEvidence {
        val hash = sha256(text)
        return RagV2VertexEvidence(1, "cit_1", "rag_v2_chk_${hash.take(32)}", text, hash)
    }

    private fun answerJson(
        text: String,
        citation: String,
        quote: String,
    ) =
        """{"basis":"EVIDENCE","answer":"$text","sentences":[{"text":"$text","citationIds":["$citation"],"evidenceSpans":[{"citationId":"$citation","quote":"$quote"}],"numericSpans":[]}],"warnings":[]}"""

    private fun providerText(json: String) = providerPart("\"text\":${jsonString(json)}")

    private fun providerFunction(
        name: String,
        args: String,
    ) = providerPart("\"functionCall\":{\"name\":\"$name\",\"args\":$args}")

    private fun providerPart(part: String) =
        """{"candidates":[{"content":{"role":"model","parts":[{$part}]}}],"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":5,"totalTokenCount":15}}"""
            .toByteArray(StandardCharsets.UTF_8)

    private fun jsonString(value: String): String =
        buildString {
            append('"')
            value.forEach { char ->
                when (char) {
                    '"' -> append("\\\"")
                    '\\' -> append("\\\\")
                    else -> append(char)
                }
            }
            append('"')
        }

    private fun sha256(value: String): String =
        MessageDigest.getInstance("SHA-256").digest(value.toByteArray()).joinToString("") { "%02x".format(it) }

    private class QueueHttpClient(
        vararg responses: ByteArray,
    ) : S49VertexHttpClient {
        private val queue = ArrayDeque(responses.toList())
        var calls = 0

        override fun generate(
            endpoint: URI,
            bearerToken: ByteArray,
            body: ByteArray,
            timeout: java.time.Duration,
        ): S49VertexHttpResponse {
            calls += 1
            bearerToken.fill(0)
            body.fill(0)
            return S49VertexHttpResponse(200, queue.removeFirst().copyOf())
        }
    }

    private class RecordingUsageLedger : S49StrongLlmUsagePort {
        var committed: S49StrongLlmUsage? = null
        var unknown = false

        override fun commit(
            ownerUserId: String,
            requestId: String,
            modelId: String,
            basis: StrongLlmAnswerBasis,
            evidence: List<RagV2VertexEvidence>,
            usage: S49StrongLlmUsage,
        ) {
            committed = usage
        }

        override fun unknownBilling(
            ownerUserId: String,
            requestId: String,
            modelId: String,
            evidence: List<RagV2VertexEvidence>,
            toolRounds: Int,
            searchCalls: Int,
            readCalls: Int,
        ) {
            unknown = true
        }
    }
}
