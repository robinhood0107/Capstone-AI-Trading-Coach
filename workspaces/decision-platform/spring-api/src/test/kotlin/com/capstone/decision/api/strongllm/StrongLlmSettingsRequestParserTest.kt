package com.capstone.decision.api.strongllm

import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test

class StrongLlmSettingsRequestParserTest {
    private val parser = StrongLlmSettingsRequestParser()

    @Test
    fun `omitted automation settings remain nullable for preserve-on-update semantics`() {
        val command = parser.parse(baseBody())

        assertThat(command.aiJudgementEnabled).isNull()
        assertThat(command.thinkingLevel).isNull()
    }

    @Test
    fun `automation settings accept the exact enabled and thinking levels`() {
        listOf("minimal", "low", "medium").forEach { level ->
            val command =
                parser.parse(
                    baseBody().dropLast(1) +
                        ",\"aiJudgementEnabled\":true,\"thinkingLevel\":\"$level\"}",
                )
            assertThat(command.aiJudgementEnabled).isTrue()
            assertThat(command.thinkingLevel).isEqualTo(level)
        }
    }

    @Test
    fun `unknown thinking level wrong boolean and duplicate keys fail closed`() {
        val invalid =
            listOf(
                baseBody().dropLast(1) + ",\"thinkingLevel\":\"high\"}",
                baseBody().dropLast(1) + ",\"aiJudgementEnabled\":\"true\"}",
                baseBody().dropLast(1) + ",\"provider\":\"vertex\"}",
            )
        invalid.forEach { body ->
            assertThatThrownBy { parser.parse(body) }.isInstanceOf(IllegalArgumentException::class.java)
        }
    }

    private fun baseBody() =
        """{"provider":"vertex","fallbackProvider":null,"modelId":"gemini-3.5-flash","fallbackModelId":null,"baseUrl":null,"fallbackBaseUrl":null,"answerLanguage":"ko","dailyGenerateCallCap":50}"""
}
