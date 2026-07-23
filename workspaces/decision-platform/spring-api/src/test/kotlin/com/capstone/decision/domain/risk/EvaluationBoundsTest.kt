package com.capstone.decision.domain.risk

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test
import java.time.Duration

class EvaluationBoundsTest {
    @Test
    fun `S22 V1 bounds are exact and versioned`() {
        assertEquals("BOUNDS-CONTRACT-S22-V1", EvaluationBounds.VERSION)
        assertEquals("^[0-9a-f]{64}$", EvaluationBounds.SANITIZED_SHA256_PATTERN)
        assertEquals(256 * 1024, EvaluationBounds.MAX_REQUEST_BYTES)
        assertEquals(1024 * 1024, EvaluationBounds.MAX_RESPONSE_BYTES)
        assertEquals(1_000, EvaluationBounds.MAX_POSITIONS)
        assertEquals(14, EvaluationBounds.MAX_VIOLATIONS)
        assertEquals(14, EvaluationBounds.MAX_ISSUES)
        assertEquals(50, EvaluationBounds.MAX_WARNINGS)
        assertEquals(50, EvaluationBounds.MAX_ABSTENTIONS)
        assertEquals(100, EvaluationBounds.MAX_DISCLOSURE_EVENTS)
        assertEquals(100, EvaluationBounds.MAX_SOURCE_REFS)
        assertEquals(128, EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        assertEquals(1_024, EvaluationBounds.MAX_SAFE_MESSAGE_CHARS)
        assertEquals(1, EvaluationBounds.MAX_LOGICAL_CALLS_PER_PORT)
        assertEquals(8, EvaluationBounds.MAX_CONCURRENCY)
        assertEquals(Duration.ofMillis(500), EvaluationBounds.SOURCE_DEADLINE)
        assertEquals(Duration.ofMillis(900), EvaluationBounds.EVALUATION_DEADLINE)
    }
}
