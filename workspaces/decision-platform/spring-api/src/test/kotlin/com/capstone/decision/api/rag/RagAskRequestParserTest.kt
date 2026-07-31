package com.capstone.decision.api.rag

import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.application.rag.RagValidationException
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.springframework.mock.web.MockHttpServletRequest
import tools.jackson.databind.json.JsonMapper

class RagAskRequestParserTest {
    private val parser = RagRequestParser()

    @Test
    fun `ask parser accepts only the four public fields and exact header pattern`() {
        val command =
            parser.parseAsk(
                """
                {
                  "question":"금 ETF의 롤오버 위험은 무엇인가요?",
                  "answerMode":"CONCISE",
                  "relatedSymbols":["132030"],
                  "topics":["PRODUCT_RISK"]
                }
                """.trimIndent(),
            )

        assertEquals("금 ETF의 롤오버 위험은 무엇인가요?", command.question)
        assertEquals("CONCISE", command.answerMode.name)
        assertEquals(listOf("132030"), command.relatedSymbols)
        assertEquals(listOf("PRODUCT_RISK"), command.topics)
        assertEquals(
            "idem-rag-ask-0001",
            parser.requireIdempotencyKey("idem-rag-ask-0001"),
        )
    }

    @Test
    fun `ask parser rejects duplicate unknown profile and provider controls`() {
        listOf(
            """{"question":"q","question":"q2","answerMode":"CONCISE"}""",
            """{"question":"q","answerMode":"CONCISE","provider":"gemini"}""",
            """{"question":"q","answerMode":"CONCISE","topK":5}""",
            """{"question":"q","answerMode":"CONCISE","profileId":"bge_m3_local_1024_v1"}""",
        ).forEach { body ->
            assertThrows(RagValidationException::class.java) {
                parser.parseAsk(body)
            }
        }
    }

    @Test
    fun `ask parser enforces NFC scalar utf8 array and header bounds`() {
        listOf(
            """{"question":"Cafe\u0301","answerMode":"CONCISE"}""",
            """{"question":"\uD800","answerMode":"CONCISE"}""",
            """{"question":"${"가".repeat(1001)}","answerMode":"CONCISE"}""",
            """{"question":"q","answerMode":"CONCISE","relatedSymbols":["NVDA"]}""",
            """{"question":"q","answerMode":"CONCISE","relatedSymbols":["005930","005930"]}""",
            """{"question":"q","answerMode":"CONCISE","topics":["UNKNOWN"]}""",
        ).forEach { body ->
            assertThrows(RagValidationException::class.java) {
                parser.parseAsk(body)
            }
        }
        listOf(
            null,
            "short",
            "x".repeat(129),
            "invalid:key:00001",
            "non ascii 한글 key 0001",
        ).forEach { key ->
            assertThrows(RagValidationException::class.java) {
                parser.requireIdempotencyKey(key)
            }
        }
    }

    @Test
    fun `history feedback and consent parsers keep exact bounded public shapes`() {
        assertEquals(
            "rag_ans_${"a".repeat(32)}",
            parser.parseAnswerId("rag_ans_${"a".repeat(32)}"),
        )
        assertEquals(true, parser.parseFeedback("""{"helpful":true}"""))
        val consent =
            parser.parseConsent(
                """
                {
                  "consentType":"EXTERNAL_AI_RAG_V1",
                  "action":"REVOKE",
                  "policyVersion":"EXTERNAL_AI_RAG_V1"
                }
                """.trimIndent(),
            )
        assertEquals("REVOKE", consent.action)
        assertEquals("EXTERNAL_AI_RAG_V1", consent.policyVersion)

        val request =
            MockHttpServletRequest().apply {
                addParameter("cursor", "opaque-cursor")
                addParameter("limit", "50")
            }
        val history = parser.parseHistoryQuery(request)
        assertEquals("opaque-cursor", history.cursor)
        assertEquals(50, history.limit)
    }

    @Test
    fun `history feedback and consent reject unknown duplicate and out of range input`() {
        listOf(
            """{"helpful":true,"comment":"no"}""",
            """{"helpful":true,"helpful":false}""",
            """{"helpful":"true"}""",
        ).forEach { body ->
            assertThrows(RagValidationException::class.java) {
                parser.parseFeedback(body)
            }
        }
        listOf(
            """{"consentType":"EXTERNAL_AI_RAG_V1","action":"GRANT","policyVersion":"v2"}""",
            """{"consentType":"LIVE_STEP1_STRATEGY_SUMMARY","action":"GRANT","policyVersion":"EXTERNAL_AI_RAG_V1"}""",
            """{"consentType":"EXTERNAL_AI_RAG_V1","action":"GRANT","policyVersion":"EXTERNAL_AI_RAG_V1","actor":"caller"}""",
        ).forEach { body ->
            assertThrows(RagValidationException::class.java) {
                parser.parseConsent(body)
            }
        }
        listOf("missing", "rag_ans_${"g".repeat(32)}", "rag_ans_${"a".repeat(31)}").forEach { answerId ->
            assertThrows(RagValidationException::class.java) {
                parser.parseAnswerId(answerId)
            }
        }

        val invalidQuery =
            MockHttpServletRequest().apply {
                addParameter("limit", "51")
                addParameter("preview", "true")
            }
        assertThrows(RagValidationException::class.java) {
            parser.parseHistoryQuery(invalidQuery)
        }
    }

    @Test
    fun `attacker controlled query validation response stays inside 32 KiB`() {
        val request =
            MockHttpServletRequest().apply {
                repeat(40) { index ->
                    addParameter("${"\\".repeat(500)}$index", "value")
                }
            }

        val exception =
            assertThrows(RagValidationException::class.java) {
                parser.requireNoQuery(request)
            }
        val envelope =
            ApiResponseFactory.error(
                requestId = "req_rag_query_budget",
                code = ErrorCode.VALIDATION_ERROR,
                details = mapOf("violations" to exception.violations),
            )

        assertTrue(exception.violations.size <= 64)
        assertTrue(
            JsonMapper
                .builder()
                .build()
                .writeValueAsBytes(envelope)
                .size <= 32 * 1_024,
        )
    }
}
