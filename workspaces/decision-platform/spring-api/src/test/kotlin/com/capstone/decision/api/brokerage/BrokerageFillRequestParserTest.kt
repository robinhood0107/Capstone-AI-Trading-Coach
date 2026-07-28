package com.capstone.decision.api.brokerage

import com.capstone.decision.application.brokerage.BrokerageValidationException
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.springframework.mock.web.MockHttpServletRequest
import java.time.Instant

class BrokerageFillRequestParserTest {
    private val parser = BrokerageFillRequestParser()

    @Test
    fun `single KST date becomes exact UTC half open interval`() {
        val request =
            MockHttpServletRequest().apply {
                addParameter("from", "2030-01-02")
                addParameter("to", "2030-01-02")
            }

        val parsed = parser.parse(request)

        assertEquals(Instant.parse("2030-01-01T15:00:00Z"), parsed.fromInclusive)
        assertEquals(Instant.parse("2030-01-02T15:00:00Z"), parsed.toExclusive)
        assertEquals(null, parsed.cursor)
    }

    @Test
    fun `thirty one days is allowed and thirty two or duplicate cursor is rejected`() {
        parser.parse(
            MockHttpServletRequest().apply {
                addParameter("from", "2030-01-01")
                addParameter("to", "2030-01-31")
            },
        )
        val tooWide =
            assertThrows<BrokerageValidationException> {
                parser.parse(
                    MockHttpServletRequest().apply {
                        addParameter("from", "2030-01-01")
                        addParameter("to", "2030-02-01")
                    },
                )
            }
        assertEquals("RANGE_EXCEEDS_31_DAYS", tooWide.violations.single().reason)

        val duplicateCursor =
            assertThrows<BrokerageValidationException> {
                parser.parse(
                    MockHttpServletRequest().apply {
                        addParameter("from", "2030-01-01")
                        addParameter("to", "2030-01-01")
                        addParameter("cursor", "first", "second")
                    },
                )
            }
        assertEquals("/query/cursor", duplicateCursor.violations.single().field)
    }
}
