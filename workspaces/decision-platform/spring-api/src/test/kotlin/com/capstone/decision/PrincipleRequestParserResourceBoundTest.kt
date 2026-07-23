package com.capstone.decision

import com.capstone.decision.api.principle.PrincipleRequestParser
import com.capstone.decision.domain.principle.PrincipleValidationException
import com.capstone.decision.domain.principle.PrincipleViolation
import com.capstone.decision.infrastructure.principle.PrincipleCatalog
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import tools.jackson.databind.json.JsonMapper
import java.math.BigDecimal

// 인증된 요청도 작은 wire 입력으로 parser 자원을 과도하게 소비하거나 500을 만들 수 없어야 한다.
class PrincipleRequestParserResourceBoundTest {
    private val parser = PrincipleRequestParser(PrincipleCatalog(JsonMapper.builder().build()))

    @Test
    fun `integer scientific notation normalizes before integer validation`() {
        val command =
            parser.parseCreate(
                """
                {
                  "presetId":"balanced",
                  "title":"정수 지수",
                  "rules":[{
                    "ruleId":"max_single_order_amount",
                    "ruleType":"ORDER_SIZE",
                    "metric":"order_amount_krw",
                    "operator":"<=",
                    "threshold":3e5,
                    "severity":"BLOCK",
                    "enabled":true
                  }]
                }
                """.trimIndent(),
            )

        assertEquals(BigDecimal("300000"), command.rules!!.single().threshold)
    }

    @Test
    fun `extreme exponent fails closed as one range violation`() {
        val exception =
            assertThrows(PrincipleValidationException::class.java) {
                parser.parseCreate(
                    """
                    {
                      "presetId":"balanced",
                      "title":"극단 지수",
                      "rules":[{
                        "ruleId":"max_position_per_asset",
                        "ruleType":"POSITION_LIMIT",
                        "metric":"asset_weight",
                        "operator":"<=",
                        "threshold":1e2147483647,
                        "severity":"BLOCK",
                        "enabled":true
                      }]
                    }
                    """.trimIndent(),
                )
            }

        assertEquals(
            listOf(PrincipleViolation("/rules/0/threshold", "OUT_OF_RANGE")),
            exception.violations,
        )
    }

    @Test
    fun `unknown property token flood stops as one invalid json violation`() {
        val body =
            buildString {
                append("""{"presetId":"balanced","title":"필드 제한"""")
                repeat(300) { index ->
                    append(",\"unknown")
                    append(index)
                    append("\":0")
                }
                append('}')
            }

        assertInvalidJson(body)
    }

    @Test
    fun `rules token flood stops before full tree materialization`() {
        val body =
            buildString {
                append("""{"presetId":"balanced","title":"규칙 제한","rules":[""")
                repeat(200) { index ->
                    if (index > 0) append(',')
                    append("{}")
                }
                append("]}")
            }

        assertInvalidJson(body)
    }

    @Test
    fun `validation response never exceeds the contract violation limit`() {
        val body =
            buildString {
                append("""{"presetId":"balanced","title":"오류 상한"""")
                repeat(70) { index ->
                    append(",\"unknown")
                    append(index.toString().padStart(2, '0'))
                    append("\":0")
                }
                append('}')
            }

        val exception =
            assertThrows(PrincipleValidationException::class.java) {
                parser.parseCreate(body)
            }

        assertEquals(64, exception.violations.size)
        assertEquals(setOf("UNKNOWN_FIELD"), exception.violations.map(PrincipleViolation::reason).toSet())
    }

    private fun assertInvalidJson(body: String) {
        val exception =
            assertThrows(PrincipleValidationException::class.java) {
                parser.parseCreate(body)
            }
        assertEquals(
            listOf(PrincipleViolation("/", "INVALID_FORMAT")),
            exception.violations,
        )
    }
}
