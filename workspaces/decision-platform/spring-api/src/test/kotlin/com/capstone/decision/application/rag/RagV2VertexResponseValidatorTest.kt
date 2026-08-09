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
            RagV2VertexEvidence(
                ordinal = 1,
                citationId = "cit_1",
                chunkRevisionId = "rag_v2_chk_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                canonicalText = "Black-Scholes assumes continuous hedging and a lognormal price process.",
                canonicalTextSha256 = sha256("Black-Scholes assumes continuous hedging and a lognormal price process."),
            ),
            RagV2VertexEvidence(
                ordinal = 2,
                citationId = "cit_2",
                chunkRevisionId = "rag_v2_chk_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                canonicalText = "The reference example uses a 5% annual risk-free rate.",
                canonicalTextSha256 = sha256("The reference example uses a 5% annual risk-free rate."),
            ),
        )

    @Test
    fun `strict Vertex JSON binds every sentence and numeric span to retrieved top five evidence`() {
        val result =
            validator.validate(
                """
                {
                  "answer":"Black-Scholes assumes continuous hedging and a lognormal price process.\nThe reference example uses a 5% annual risk-free rate.",
                  "sentences":[
                    {
                      "text":"Black-Scholes assumes continuous hedging and a lognormal price process.",
                      "citationIds":["cit_1"],
                      "numericSpans":[]
                    },
                    {
                      "text":"The reference example uses a 5% annual risk-free rate.",
                      "citationIds":["cit_2"],
                      "numericSpans":[{"value":"5%","citationIds":["cit_2"]}]
                    }
                  ]
                }
                """.trimIndent(),
                evidence,
            )

        assertThat(result.answer).contains("5%")
        assertThat(result.citationIds).containsExactly("cit_1", "cit_2")
    }

    @Test
    fun `strict Vertex JSON rejects an ungrounded citation or numeric span before history persistence`() {
        assertThatThrownBy {
            validator.validate(
                """
                {
                  "answer":"The cited example uses a 7.5% annual risk-free rate.",
                  "sentences":[
                    {
                      "text":"The cited example uses a 7.5% annual risk-free rate.",
                      "citationIds":["cit_3"],
                      "numericSpans":[{"value":"7.5%","citationIds":["cit_3"]}]
                    }
                  ]
                }
                """.trimIndent(),
                evidence,
            )
        }.isInstanceOf(RagV2VertexResponseValidationException::class.java)
    }

    @Test
    fun `numeric span must use a citation from its own sentence rather than another top five item`() {
        assertThatThrownBy {
            validator.validate(
                """
                {
                  "answer":"The cited example uses a 5% annual risk-free rate.",
                  "sentences":[
                    {
                      "text":"The cited example uses a 5% annual risk-free rate.",
                      "citationIds":["cit_1"],
                      "numericSpans":[{"value":"5%","citationIds":["cit_2"]}]
                    }
                  ]
                }
                """.trimIndent(),
                evidence,
            )
        }.isInstanceOf(RagV2VertexResponseValidationException::class.java)
    }

    @Test
    fun `strict Vertex JSON blocks direct personalized trading advice even when citations are syntactically valid`() {
        assertThatThrownBy {
            validator.validate(
                """
                {
                  "answer":"지금 매수해야 합니다.",
                  "sentences":[
                    {
                      "text":"지금 매수해야 합니다.",
                      "citationIds":["cit_1"],
                      "numericSpans":[]
                    }
                  ]
                }
                """.trimIndent(),
                evidence,
            )
        }.isInstanceOf(RagV2VertexResponseValidationException::class.java)
    }

    @Test
    fun `strict Vertex JSON blocks imperative Korean and English investment advice even when the source text repeats it`() {
        listOf(
            "이 종목을 매수하세요.",
            "매도하고 현금화하세요.",
            "Buy this stock immediately.",
            "You ought to acquire this stock.",
            "이 종목을 매입하는 것이 좋습니다.",
            "Consider purchasing this stock.",
            "Invest in this ETF.",
            "You should invest in this stock.",
            "이 주식을 사는 것을 추천합니다.",
            "이 ETF에 투자하세요.",
            "이 종목에 투자하세요.",
        ).forEach { advice ->
            assertThatThrownBy {
                validator.validate(
                    """
                    {
                      "answer":"$advice",
                      "sentences":[
                        {"text":"$advice","citationIds":["cit_1"],"numericSpans":[]}
                      ]
                    }
                    """.trimIndent(),
                    evidenceForSentence(advice),
                )
            }.isInstanceOf(RagV2VertexResponseValidationException::class.java)
        }
    }

    @Test
    fun `numeric values must occur in the canonical evidence cited by their span`() {
        assertThatThrownBy {
            validator.validate(
                """
                {
                  "answer":"The reference example uses a 99% annual risk-free rate.",
                  "sentences":[
                    {
                      "text":"The reference example uses a 99% annual risk-free rate.",
                      "citationIds":["cit_2"],
                      "numericSpans":[{"value":"99%","citationIds":["cit_2"]}]
                    }
                  ]
                }
                """.trimIndent(),
                evidence,
            )
        }.isInstanceOf(RagV2VertexResponseValidationException::class.java)
    }

    private fun evidenceForSentence(sentence: String): List<RagV2VertexEvidence> =
        listOf(
            RagV2VertexEvidence(
                ordinal = 1,
                citationId = "cit_1",
                chunkRevisionId = "rag_v2_chk_cccccccccccccccccccccccccccccccc",
                canonicalText = sentence,
                canonicalTextSha256 = sha256(sentence),
            ),
        )

    private fun sha256(value: String): String =
        MessageDigest
            .getInstance("SHA-256")
            .digest(value.toByteArray(StandardCharsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
}
