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
            """{"basis":"EVIDENCE","answer":"공분산 항은 포트폴리오 위험을 구성합니다.","sentences":[{"text":"공분산 항은 포트폴리오 위험을 구성합니다.","citationIds":["cit_1","cit_2"],"evidenceSpans":[{"citationId":"cit_2","quote":"The covariance terms determine how pairs of assets contribute to total portfolio risk."}],"numericSpans":[]}],"warnings":[]}""",
        ).forEach { body ->
            assertThatThrownBy { validator.validate(body, evidence) }
                .isInstanceOf(RagV2VertexResponseValidationException::class.java)
        }
    }

    @Test
    fun `validation failures expose only a stable content free boundary leaf`() {
        assertThatThrownBy {
            validator.validate(
                """{"basis":"EVIDENCE","answer":"근거가 없습니다.","sentences":[{"text":"근거가 없습니다.","citationIds":["cit_1"],"evidenceSpans":[{"citationId":"cit_1","quote":"fabricated quote"}],"numericSpans":[]}],"warnings":[]}""",
                evidence,
            )
        }.isInstanceOf(RagV2VertexResponseValidationException::class.java)
            .hasMessage("STRONG_LLM_VALIDATION_EVIDENCE_SPAN")
    }

    @Test
    fun `model knowledge is citation free and may carry numbers and time words`() {
        val result =
            validator.validate(
                """{"basis":"MODEL_KNOWLEDGE","answer":"분산투자는 서로 다른 위험 요인을 가진 자산을 함께 구성하는 일반적인 위험 관리 개념입니다.","sentences":[{"text":"분산투자는 서로 다른 위험 요인을 가진 자산을 함께 구성하는 일반적인 위험 관리 개념입니다.","citationIds":[],"evidenceSpans":[],"numericSpans":[]}],"warnings":[]}""",
                evidence,
            )
        assertThat(result.citationIds).isEmpty()
        assertThat(result.citationCoverage).isZero()

        // 숫자와 시점 표현을 막던 규칙은 뺐다. 그 규칙이 실제로 막은 것은 조작된 수치가
        // 아니라 롤오버는 만기 3개월 전에 한다 같은 평범한 설명이었고, 그때마다 답이
        // 통째로 사라졌다. 이 basis는 인용이 없다는 사실을 스스로 밝히므로 읽는 사람은
        // 그 문장이 근거에 결속되지 않았음을 안다.
        listOf("최근 채권 비중을 늘리는 논의가 있습니다.", "주식 비중 60%는 흔히 쓰이는 예시입니다.").forEach { answer ->
            val plain =
                validator.validate(
                    """{"basis":"MODEL_KNOWLEDGE","answer":"$answer","sentences":[{"text":"$answer","citationIds":[],"evidenceSpans":[],"numericSpans":[]}],"warnings":[]}""",
                    evidence,
                )

            assertThat(plain.basis).isEqualTo(StrongLlmAnswerBasis.MODEL_KNOWLEDGE)
            assertThat(plain.answer).isEqualTo(answer)
            assertThat(plain.citationIds).isEmpty()
            assertThat(plain.citationCoverage).isZero()
        }
    }

    @Test
    fun `timeless model knowledge is valid when retrieval supplied no evidence`() {
        val result =
            validator.validate(
                """{"basis":"MODEL_KNOWLEDGE","answer":"분산투자는 서로 다른 위험 요인을 함께 구성하는 일반적인 위험 관리 개념입니다.","sentences":[{"text":"분산투자는 서로 다른 위험 요인을 함께 구성하는 일반적인 위험 관리 개념입니다.","citationIds":[],"evidenceSpans":[],"numericSpans":[]}],"warnings":[]}""",
                emptyList(),
            )

        assertThat(result.basis).isEqualTo(StrongLlmAnswerBasis.MODEL_KNOWLEDGE)
        assertThat(result.citationIds).isEmpty()
        assertThat(result.citationCoverage).isZero()

        assertThatThrownBy {
            validator.validate(
                """{"basis":"EVIDENCE","answer":"근거가 있습니다.","sentences":[{"text":"근거가 있습니다.","citationIds":["cit_1"],"evidenceSpans":[{"citationId":"cit_1","quote":"근거"}],"numericSpans":[]}],"warnings":[]}""",
                emptyList(),
            )
        }.isInstanceOf(RagV2VertexResponseValidationException::class.java)
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
    fun `advice phrasing no longer discards a properly cited answer`() {
        // 다 만들어진 설명을 사후에 통째로 버리는 것은 조언 경계를 지키는 방법이 아니라
        // 사용자가 아무것도 못 읽게 하는 방법이었다. 그 경계는 프롬프트가 세우고 동의
        // 고지가 말한다. 여기 남는 검사는 PII 유출 방지 하나다.
        val adviceEvidence = listOf(evidence(1, "d", "지금 이 종목을 매수해야 합니다."))
        val result =
            validator.validate(
                """{"basis":"EVIDENCE","answer":"지금 이 종목을 매수해야 합니다.","sentences":[{"text":"지금 이 종목을 매수해야 합니다.","citationIds":["cit_1"],"evidenceSpans":[{"citationId":"cit_1","quote":"지금 이 종목을 매수해야 합니다."}],"numericSpans":[]}],"warnings":[]}""",
                adviceEvidence,
            )

        assertThat(result.basis).isEqualTo(StrongLlmAnswerBasis.EVIDENCE)
        assertThat(result.citationIds).containsExactly("cit_1")
    }

    @Test
    fun `validated answer cannot exceed the public service byte boundary`() {
        // 출력 예산이 32,768 토큰으로 올라가도 답 본문의 경계는 그대로다. 예산은 잘림을
        // 막으려고 넓힌 것이지 답을 길게 하려는 것이 아니다.
        val sentence = "근거문장".repeat(160)
        val longEvidence = listOf(evidence(1, "e", sentence))
        val sentences =
            List(5) {
                """{"text":"$sentence","citationIds":["cit_1"],"evidenceSpans":[{"citationId":"cit_1","quote":"$sentence"}],"numericSpans":[]}"""
            }
        val answer = List(5) { sentence }.joinToString("\n")

        assertThat(answer.toByteArray(StandardCharsets.UTF_8).size).isGreaterThan(8_192)
        val response =
            """{"basis":"EVIDENCE","answer":"${answer.replace("\n", "\\n")}","sentences":[${
                sentences.joinToString(",")
            }],"warnings":[]}"""
        assertThatThrownBy {
            validator.validate(
                response,
                longEvidence,
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
            "%02x".format(java.util.Locale.ROOT, it)
        }

    @Test
    fun `a reasoning sentence may sit beside a grounded one without carrying a citation`() {
        // EVIDENCE는 모든 문장에 정확 인용을 요구해서 근거를 잇거나 한계를 말하는 문장을 아예
        // 쓸 수 없었다. 그래서 답이 인용의 나열이 되고 Strong LLM을 쓰는 이유가 사라진다.
        val result =
            validator.validate(
                """
                {
                  "basis":"EVIDENCE_WITH_REASONING",
                  "answer":"공분산 항은 포트폴리오 위험을 구성합니다.\n따라서 상관관계가 낮은 자산을 섞을수록 이 항의 기여가 줄어드는 방향으로 움직입니다.",
                  "sentences":[
                    {
                      "text":"공분산 항은 포트폴리오 위험을 구성합니다.",
                      "citationIds":["cit_2"],
                      "evidenceSpans":[
                        {"citationId":"cit_2","quote":"The covariance terms determine how pairs of assets contribute to total portfolio risk."}
                      ],
                      "numericSpans":[]
                    },
                    {
                      "text":"따라서 상관관계가 낮은 자산을 섞을수록 이 항의 기여가 줄어드는 방향으로 움직입니다.",
                      "citationIds":[],
                      "evidenceSpans":[],
                      "numericSpans":[]
                    }
                  ],
                  "warnings":[]
                }
                """.trimIndent(),
                evidence,
            )

        assertThat(result.basis).isEqualTo(StrongLlmAnswerBasis.EVIDENCE_WITH_REASONING)
        assertThat(result.citationIds).containsExactly("cit_2")
        // 두 문장 중 하나만 근거 문장이므로 화면은 그 비율을 그대로 보여줄 수 있다.
        assertThat(result.citationCoverage).isEqualTo(0.5)
    }

    @Test
    fun `a reasoning sentence may reuse a number the grounded sentences already proved`() {
        val result =
            validator.validate(
                """
                {
                  "basis":"EVIDENCE_WITH_REASONING",
                  "answer":"예시는 연 5%를 사용합니다.\n같은 5%를 다른 가정에 적용하면 결과가 달라질 수 있습니다.",
                  "sentences":[
                    {
                      "text":"예시는 연 5%를 사용합니다.",
                      "citationIds":["cit_3"],
                      "evidenceSpans":[{"citationId":"cit_3","quote":"5% annual risk-free rate"}],
                      "numericSpans":[{"value":"5%","citationIds":["cit_3"]}]
                    },
                    {
                      "text":"같은 5%를 다른 가정에 적용하면 결과가 달라질 수 있습니다.",
                      "citationIds":[],
                      "evidenceSpans":[],
                      "numericSpans":[]
                    }
                  ],
                  "warnings":[]
                }
                """.trimIndent(),
                evidence,
            )

        assertThat(result.basis).isEqualTo(StrongLlmAnswerBasis.EVIDENCE_WITH_REASONING)
    }

    @Test
    fun `a reasoning sentence may carry a number or a time word`() {
        // 인용 없는 문장의 숫자·시점 제약을 뺐다. 그 문장은 citationIds가 비어 있다는
        // 사실로 이미 근거에 결속되지 않았음을 말하고 있고, 여기서 닫으면 인용을 제대로
        // 갖춘 근거 문장까지 포함한 답 전체가 사라졌다. 근거 문장이 quote·숫자 검증을
        // 그대로 통과해야 하는 규칙은 남아 있다.
        val numeric =
            validator.validate(
                """
                {
                  "basis":"EVIDENCE_WITH_REASONING",
                  "answer":"공분산 항은 포트폴리오 위험을 구성합니다.\n따라서 분산은 대략 42% 줄어듭니다.",
                  "sentences":[
                    {
                      "text":"공분산 항은 포트폴리오 위험을 구성합니다.",
                      "citationIds":["cit_2"],
                      "evidenceSpans":[
                        {"citationId":"cit_2","quote":"The covariance terms determine how pairs of assets contribute to total portfolio risk."}
                      ],
                      "numericSpans":[]
                    },
                    {
                      "text":"따라서 분산은 대략 42% 줄어듭니다.",
                      "citationIds":[],
                      "evidenceSpans":[],
                      "numericSpans":[]
                    }
                  ],
                  "warnings":[]
                }
                """.trimIndent(),
                evidence,
            )

        assertThat(numeric.basis).isEqualTo(StrongLlmAnswerBasis.EVIDENCE_WITH_REASONING)
        assertThat(numeric.citationIds).containsExactly("cit_2")

        val timely =
            validator.validate(
                """
                {
                  "basis":"EVIDENCE_WITH_REASONING",
                  "answer":"공분산 항은 포트폴리오 위험을 구성합니다.\n최근 시장에서도 같은 관계가 유지되고 있습니다.",
                  "sentences":[
                    {
                      "text":"공분산 항은 포트폴리오 위험을 구성합니다.",
                      "citationIds":["cit_2"],
                      "evidenceSpans":[
                        {"citationId":"cit_2","quote":"The covariance terms determine how pairs of assets contribute to total portfolio risk."}
                      ],
                      "numericSpans":[]
                    },
                    {
                      "text":"최근 시장에서도 같은 관계가 유지되고 있습니다.",
                      "citationIds":[],
                      "evidenceSpans":[],
                      "numericSpans":[]
                    }
                  ],
                  "warnings":[]
                }
                """.trimIndent(),
                evidence,
            )

        assertThat(timely.basis).isEqualTo(StrongLlmAnswerBasis.EVIDENCE_WITH_REASONING)
        assertThat(timely.citationIds).containsExactly("cit_2")
    }

    @Test
    fun `an answer with no grounded sentence at all is not reasoning over evidence`() {
        // 근거 문장이 하나도 없으면 그것은 추론이 아니라 MODEL_KNOWLEDGE이고, 그 basis에는
        // 숫자와 시점 금지가 그대로 걸린다. 여기로 새는 길을 열지 않는다.
        assertThatThrownBy {
            validator.validate(
                """{"basis":"EVIDENCE_WITH_REASONING","answer":"분산투자는 위험을 줄입니다.","sentences":[{"text":"분산투자는 위험을 줄입니다.","citationIds":[],"evidenceSpans":[],"numericSpans":[]}],"warnings":[]}""",
                evidence,
            )
        }.isInstanceOf(RagV2VertexResponseValidationException::class.java)
    }

    @Test
    fun `a grounded sentence keeps the exact quote requirement inside the new basis`() {
        assertThatThrownBy {
            validator.validate(
                """{"basis":"EVIDENCE_WITH_REASONING","answer":"근거가 없습니다.\n그래서 결론을 미룹니다.","sentences":[{"text":"근거가 없습니다.","citationIds":["cit_1"],"evidenceSpans":[{"citationId":"cit_1","quote":"fabricated quote"}],"numericSpans":[]},{"text":"그래서 결론을 미룹니다.","citationIds":[],"evidenceSpans":[],"numericSpans":[]}],"warnings":[]}""",
                evidence,
            )
        }.isInstanceOf(RagV2VertexResponseValidationException::class.java)
    }
}
