package com.capstone.decision.application.rag

import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import java.nio.charset.StandardCharsets
import java.security.MessageDigest

class RagV2VertexResponseValidatorTest {
    private val validator = RagV2VertexResponseValidator()
    private val evidence =
        listOf(
            evidence(1, "a", "Diversification can reduce portfolio variance when asset returns are not perfectly correlated."),
            evidence(2, "b", "The covariance terms determine how pairs of assets contribute to total portfolio risk."),
            evidence(3, "c", "The reference example uses a 5% annual risk-free rate."),
        )

    @Test
    fun `paraphrase may synthesize multiple top five items when exact evidence spans support every sentence`() {
        val result =
            validator.validate(
                """
                {
                  "basis":"EVIDENCE",
                  "answer":"완전한 양의 상관관계가 아닌 자산을 함께 보유하면 공분산 항이 달라져 포트폴리오 분산을 낮출 수 있습니다.",
                  "sentences":[{
                    "text":"완전한 양의 상관관계가 아닌 자산을 함께 보유하면 공분산 항이 달라져 포트폴리오 분산을 낮출 수 있습니다.",
                    "citationIds":["cit_1","cit_2"],
                    "evidenceSpans":[
                      {"citationId":"cit_1","quote":"when asset returns are not perfectly correlated"},
                      {"citationId":"cit_2","quote":"The covariance terms determine how pairs of assets contribute to total portfolio risk."}
                    ],
                    "numericSpans":[]
                  }],
                  "warnings":[]
                }
                """.trimIndent(),
                evidence,
            )

        assertThat(result.basis).isEqualTo(StrongLlmAnswerBasis.EVIDENCE)
        assertThat(result.citationIds).containsExactly("cit_1", "cit_2")
        assertThat(result.citationCoverage).isEqualTo(1.0)
    }

    @Test
    fun `numeric statement requires the same exact value in a submitted evidence quote`() {
        val valid =
            validator.validate(
                """{"basis":"EVIDENCE","answer":"예시는 연 5%를 사용합니다.","sentences":[{"text":"예시는 연 5%를 사용합니다.","citationIds":["cit_3"],"evidenceSpans":[{"citationId":"cit_3","quote":"5% annual risk-free rate"}],"numericSpans":[{"value":"5%","citationIds":["cit_3"]}]}],"warnings":["SINGLE_SOURCE"]}""",
                evidence,
            )
        assertThat(valid.validationStatus).isEqualTo(StrongLlmValidationStatus.VALID_WITH_WARNINGS)

        assertThatThrownBy {
            validator.validate(
                """{"basis":"EVIDENCE","answer":"예시는 연 7%를 사용합니다.","sentences":[{"text":"예시는 연 7%를 사용합니다.","citationIds":["cit_3"],"evidenceSpans":[{"citationId":"cit_3","quote":"5% annual risk-free rate"}],"numericSpans":[{"value":"7%","citationIds":["cit_3"]}]}],"warnings":[]}""",
                evidence,
            )
        }.isInstanceOf(RagV2VertexResponseValidationException::class.java)
    }

    @Test
    fun `fabricated quote citation and cross citation number are invalid`() {
        listOf(
            """{"basis":"EVIDENCE","answer":"분산이 항상 0이 됩니다.","sentences":[{"text":"분산이 항상 0이 됩니다.","citationIds":["cit_1"],"evidenceSpans":[{"citationId":"cit_1","quote":"variance always becomes zero"}],"numericSpans":[{"value":"0","citationIds":["cit_1"]}]}],"warnings":[]}""",
            """{"basis":"EVIDENCE","answer":"예시는 연 5%를 사용합니다.","sentences":[{"text":"예시는 연 5%를 사용합니다.","citationIds":["cit_1","cit_3"],"evidenceSpans":[{"citationId":"cit_1","quote":"portfolio variance"}],"numericSpans":[{"value":"5%","citationIds":["cit_3"]}]}],"warnings":[]}""",
        ).forEach { body ->
            assertThatThrownBy { validator.validate(body, evidence) }
                .isInstanceOf(RagV2VertexResponseValidationException::class.java)
        }
    }

    @Test
    fun `timeless model knowledge is citation free but current or numeric claims are rejected`() {
        val result =
            validator.validate(
                """{"basis":"MODEL_KNOWLEDGE","answer":"분산투자는 서로 다른 위험 요인을 가진 자산을 함께 구성하는 일반적인 위험 관리 개념입니다.","sentences":[{"text":"분산투자는 서로 다른 위험 요인을 가진 자산을 함께 구성하는 일반적인 위험 관리 개념입니다.","citationIds":[],"evidenceSpans":[],"numericSpans":[]}],"warnings":[]}""",
                evidence,
            )
        assertThat(result.citationIds).isEmpty()
        assertThat(result.citationCoverage).isZero()

        listOf("현재 가장 좋은 자산은 채권입니다.", "주식 비중은 60%가 적절합니다.").forEach { answer ->
            val numeric = Regex("[-+]?\\d+(?:\\.\\d+)?%?").findAll(answer).map { it.value }.toList()
            val numericJson = numeric.joinToString(",") { "{\"value\":\"$it\",\"citationIds\":[]}" }
            assertThatThrownBy {
                validator.validate(
                    """{"basis":"MODEL_KNOWLEDGE","answer":"$answer","sentences":[{"text":"$answer","citationIds":[],"evidenceSpans":[],"numericSpans":[$numericJson]}],"warnings":[]}""",
                    evidence,
                )
            }.isInstanceOf(RagV2VertexResponseValidationException::class.java)
        }
    }

    @Test
    fun `general portfolio terminology is not mistaken for a credential or direct advice`() {
        val result =
            validator.validate(
                """{"basis":"MODEL_KNOWLEDGE","answer":"포트폴리오 보유 종목은 서로 다른 위험 요인에 노출될 수 있습니다.","sentences":[{"text":"포트폴리오 보유 종목은 서로 다른 위험 요인에 노출될 수 있습니다.","citationIds":[],"evidenceSpans":[],"numericSpans":[]}],"warnings":[]}""",
                evidence,
            )

        assertThat(result.basis).isEqualTo(StrongLlmAnswerBasis.MODEL_KNOWLEDGE)
    }

    @Test
    fun `insufficient evidence is a null answer with no fabricated citation`() {
        val result =
            validator.validate(
                """{"basis":"INSUFFICIENT_EVIDENCE","answer":null,"sentences":[],"warnings":["LOW_RELEVANCE"]}""",
                evidence,
            )
        assertThat(result.answer).isNull()
        assertThat(result.citationIds).isEmpty()
    }

    @Test
    fun `direct personalized trading advice remains invalid even with a real quote`() {
        val adviceEvidence = listOf(evidence(1, "d", "지금 이 종목을 매수해야 합니다."))
        assertThatThrownBy {
            validator.validate(
                """{"basis":"EVIDENCE","answer":"지금 이 종목을 매수해야 합니다.","sentences":[{"text":"지금 이 종목을 매수해야 합니다.","citationIds":["cit_1"],"evidenceSpans":[{"citationId":"cit_1","quote":"지금 이 종목을 매수해야 합니다."}],"numericSpans":[]}],"warnings":[]}""",
                adviceEvidence,
            )
        }.isInstanceOf(RagV2VertexResponseValidationException::class.java)
    }

    private fun evidence(
        ordinal: Int,
        idSeed: String,
        text: String,
    ): RagV2VertexEvidence =
        RagV2VertexEvidence(
            ordinal = ordinal,
            citationId = "cit_$ordinal",
            chunkRevisionId = "rag_v2_chk_${idSeed.repeat(32)}",
            canonicalText = text,
            canonicalTextSha256 = sha256(text),
        )

    private fun sha256(value: String): String =
        MessageDigest.getInstance("SHA-256").digest(value.toByteArray(StandardCharsets.UTF_8)).joinToString("") {
            "%02x".format(it)
        }
}
