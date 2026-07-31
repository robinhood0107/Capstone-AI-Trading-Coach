package com.capstone.decision.api.rag

import com.capstone.decision.application.rag.RagValidationException
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test

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
}
