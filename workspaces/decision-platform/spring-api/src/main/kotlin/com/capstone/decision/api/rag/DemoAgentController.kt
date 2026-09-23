package com.capstone.decision.api.rag

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.rag.RagAnswerMode
import com.capstone.decision.application.rag.RagGenerationStatus
import com.capstone.decision.application.rag.RagGuardHistoryUnavailableException
import com.capstone.decision.application.rag.RagRateLimitPort
import com.capstone.decision.application.rag.RagRateLimitedException
import com.capstone.decision.application.rag.RagV2EffectiveConsent
import com.capstone.decision.application.rag.RagV2RetrievalScope
import com.capstone.decision.application.rag.RagV2VertexEvidence
import com.capstone.decision.application.rag.RagV2VertexGenerationCommand
import com.capstone.decision.infrastructure.grpc.DEMO_INTERNAL_OWNER_USER_ID
import com.capstone.decision.infrastructure.grpc.GrpcStrongLlmGenerationAdapter
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.springframework.context.annotation.Profile
import org.springframework.http.MediaType
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import tools.jackson.core.JacksonException
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.json.JsonMapper
import java.nio.charset.StandardCharsets
import java.security.MessageDigest

data class DemoAgentCitation(
    val citationId: String,
    val title: String,
    val url: String,
)

data class DemoAgentAnswer(
    val answer: String,
    val citations: List<DemoAgentCitation>,
    val generationStatus: String,
)

private enum class DemoQuestion(val id: String, val prompt: String) {
    DIVERSIFICATION("diversification", "분산투자는 위험을 어떻게 줄이나요?"),
    ASSET_ALLOCATION("asset_allocation", "자산 배분을 정할 때 무엇을 고려하나요?"),
    PAST_PERFORMANCE(
        "past_performance",
        "과거 수익과 백테스트 결과는 어떻게 해석해야 하나요?",
    ),
}

/** Only public example evidence crosses the anonymous demo provider boundary. */
@RestController
@Profile("mars-demo")
@RequestMapping("/api/v1/demo/agent", produces = [MediaType.APPLICATION_JSON_VALUE])
internal class DemoAgentController(
    private val generation: GrpcStrongLlmGenerationAdapter,
    private val rateLimiter: RagRateLimitPort,
) {
    @PostMapping("/ask", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun ask(
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
        response: HttpServletResponse,
    ): ApiResponse<DemoAgentAnswer> {
        response.setHeader("Cache-Control", "no-store")
        val question = parseQuestion(body)
        try {
            // One synthetic scope makes Redis's existing atomic minute bucket a
            // server-wide demo limit, without storing or trusting a visitor IP.
            rateLimiter.acquire(DEMO_INTERNAL_OWNER_USER_ID)
        } catch (_: RagRateLimitedException) {
            throw ApiException(ErrorCode.RATE_LIMITED)
        } catch (_: RagGuardHistoryUnavailableException) {
            throw ApiException(ErrorCode.RAG_UNAVAILABLE)
        }
        val requestId = RequestIds.currentOrCreate(request)
        // A visitor may repeat X-Request-Id. Provider/budget identity must remain
        // server-generated so one visitor cannot poison another request's reservation.
        val providerRequestId = RequestIds.generate()
        val scopeHash = sha256(providerRequestId).take(32)
        val command =
            RagV2VertexGenerationCommand(
                ownerUserId = DEMO_INTERNAL_OWNER_USER_ID,
                requestId = providerRequestId,
                question = question.prompt,
                answerMode = RagAnswerMode.CONCISE,
                scope =
                    RagV2RetrievalScope(
                        scopeClaimId = "rvs_$scopeHash",
                        exact30GenerationId = "demo_examples_v1",
                        oa112GenerationId = "demo_examples_v1",
                        ownerGenerationId = null,
                        embeddingProfileId = "demo_examples_v1",
                        policyVersion = 1,
                    ),
                consent =
                    RagV2EffectiveConsent(
                        consentEventId = "demo_public_examples",
                        effective = false,
                        policyDigest = ZERO_SHA256,
                        processorSetDigest = ZERO_SHA256,
                        state = "NOT_REQUIRED",
                    ),
                evidence = EXAMPLE_EVIDENCE,
            )
        val result =
            try {
                generation.generate(command)
            } catch (_: Exception) {
                throw ApiException(ErrorCode.PYTHON_SERVICE_UNAVAILABLE)
            }
        if (result.failureCode == "DEMO_AI_BUDGET_EXHAUSTED") {
            throw ApiException(ErrorCode.RATE_LIMITED)
        }
        when (result.generationStatus) {
            RagGenerationStatus.ANSWERED, RagGenerationStatus.RETRIEVAL_ONLY -> Unit
            RagGenerationStatus.BLOCKED_ADVICE, RagGenerationStatus.BLOCKED_SENSITIVE ->
                throw ApiException(ErrorCode.RISK_BLOCKED)
            RagGenerationStatus.RETRIEVAL_FAILURE -> throw ApiException(ErrorCode.RAG_UNAVAILABLE)
            RagGenerationStatus.GENERATION_UNAVAILABLE -> throw ApiException(ErrorCode.PYTHON_SERVICE_UNAVAILABLE)
        }
        val answer = result.answer ?: "예제 근거만으로는 답을 확정할 수 없습니다."
        if (answer.length > 8_192) throw ApiException(ErrorCode.PYTHON_SERVICE_UNAVAILABLE)
        val citationIds = result.citationIds.distinct()
        val citations =
            citationIds.mapNotNull { id ->
                EXAMPLE_EVIDENCE.firstOrNull { it.citationId == id }?.let { evidence ->
                    DemoAgentCitation(id, requireNotNull(evidence.title), requireNotNull(evidence.canonicalUrl))
                }
            }
        if (citations.size != citationIds.size) {
            throw ApiException(ErrorCode.PYTHON_SERVICE_UNAVAILABLE)
        }
        return ApiResponseFactory.success(
            requestId,
            DemoAgentAnswer(answer, citations, result.generationStatus.name),
        )
    }

    private fun parseQuestion(body: String?): DemoQuestion {
        if (body == null || body.length > 256 || body.toByteArray(StandardCharsets.UTF_8).size > 256) {
            throw ApiException(ErrorCode.VALIDATION_ERROR)
        }
        val root =
            try {
                MAPPER.readTree(body)
            } catch (_: JacksonException) {
                throw ApiException(ErrorCode.VALIDATION_ERROR)
            } catch (_: IllegalArgumentException) {
                throw ApiException(ErrorCode.VALIDATION_ERROR)
            }
        if (root == null || !root.isObject || root.properties().map { it.key }.toSet() != setOf("questionId")) {
            throw ApiException(ErrorCode.VALIDATION_ERROR)
        }
        val raw =
            root.get("questionId")?.takeIf { it.isString }?.stringValue()
                ?: throw ApiException(ErrorCode.VALIDATION_ERROR)
        return DemoQuestion.entries.firstOrNull { it.id == raw }
            ?: throw ApiException(ErrorCode.VALIDATION_ERROR)
    }

    private companion object {
        val ZERO_SHA256 = "0".repeat(64)
        val MAPPER =
            JsonMapper
                .builder(
                    JsonFactory
                        .builder()
                        .streamReadConstraints(
                            StreamReadConstraints
                                .builder()
                                .maxDocumentLength(256)
                                .maxNestingDepth(2)
                                .maxTokenCount(16)
                                .maxStringLength(32)
                                .build(),
                        ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                        .build(),
                ).build()
        val EXAMPLE_EVIDENCE =
            listOf(
                example(
                    1,
                    "자산 배분은 투자금을 주식, 채권, 현금 같은 자산군에 나누는 것입니다. 알맞은 비율은 투자 기간과 위험 감수 성향에 따라 달라집니다.",
                    "Investor.gov — Asset Allocation and Diversification",
                    "https://www.investor.gov/introduction-investing/getting-started/asset-allocation",
                ),
                example(
                    2,
                    "분산투자는 여러 투자에 돈을 나누어 위험 집중을 줄이는 방법입니다. 시장 전체가 하락하면 분산해도 손실을 피한다는 보장은 없습니다.",
                    "Investor.gov — Diversify Your Investments",
                    "https://www.investor.gov/introduction-investing/investing-basics/" +
                        "save-and-invest/diversify-your-investments",
                ),
                example(
                    3,
                    "과거 수익과 백테스트 결과는 미래 성과를 보장하지 않습니다. 백테스트는 과거 시장 상황을 가정한 결과이며 실제 운용 성과가 아닙니다.",
                    "Investor.gov — Performance Claims",
                    "https://www.investor.gov/introduction-investing/general-resources/" +
                        "news-alerts/alerts-bulletins/investor-bulletins-47",
                ),
            )

        fun example(
            ordinal: Int,
            text: String,
            title: String,
            url: String,
        ): RagV2VertexEvidence {
            val digest = sha256(text)
            return RagV2VertexEvidence(
                ordinal = ordinal,
                citationId = "cit_$ordinal",
                chunkRevisionId = "rag_v2_chk_${digest.take(32)}",
                canonicalText = text,
                canonicalTextSha256 = digest,
                title = title,
                canonicalUrl = url,
            )
        }

        fun sha256(value: String): String =
            MessageDigest
                .getInstance("SHA-256")
                .digest(value.toByteArray(StandardCharsets.UTF_8))
                .joinToString("") { "%02x".format(java.util.Locale.ROOT, it) }
    }
}
