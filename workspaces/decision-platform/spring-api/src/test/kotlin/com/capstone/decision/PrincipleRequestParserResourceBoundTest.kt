package com.capstone.decision

import com.capstone.decision.api.principle.PrincipleRequestParser
import com.capstone.decision.domain.principle.PrincipleValidationException
import com.capstone.decision.domain.principle.PrincipleViolation
import com.capstone.decision.infrastructure.principle.PrincipleCatalog
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import tools.jackson.databind.json.JsonMapper

// 인증된 요청도 작은 wire 입력으로 parser 자원을 과도하게 소비하거나 500을 만들 수 없어야 한다.
class PrincipleRequestParserResourceBoundTest {
    private val parser = PrincipleRequestParser(PrincipleCatalog(JsonMapper.builder().build()))

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
