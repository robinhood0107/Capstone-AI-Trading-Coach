package com.capstone.decision.api.risk

import com.capstone.decision.application.risk.RiskValidationException
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test

class RiskRequestParserTest {
    private val parser = RiskRequestParser()

    @Test
    fun `parser accepts exact body and optional reason`() {
        val withReason = parser.parseKillSwitchChange("""{"active":true,"reason":"시연 중 안전 정지"}""")
        assertEquals(true, withReason.active)
        assertEquals("시연 중 안전 정지", withReason.reason)

        val withoutReason = parser.parseKillSwitchChange("""{"active":false}""")
        assertEquals(false, withoutReason.active)
        assertNull(withoutReason.reason)
    }

    @Test
    fun `parser rejects duplicate unknown injected and malformed fields without reflecting values`() {
        listOf(
            """{"active":true,"active":false}""",
            """{"active":true,"changedBy":"usr_attacker"}""",
            """{"active":"true"}""",
            """{"active":true,"reason":null}""",
            """{"active":true,"reason":"line\nbreak"}""",
            """{"active":true,"reason":"${"가".repeat(201)}"}""",
            """{"reason":"missing active"}""",
            """[]""",
        ).forEach { body ->
            val exception =
                assertThrows(RiskValidationException::class.java) {
                    parser.parseKillSwitchChange(body)
                }
            assertEquals(false, exception.message.orEmpty().contains("usr_attacker"))
        }
    }
}
