package com.capstone.decision.domain.risk

import java.time.Duration

// 승인된 S22 V1 자원 상한이며 변경 시 contract version과 fixture를 함께 올린다.
object EvaluationBounds {
    const val VERSION = "BOUNDS-CONTRACT-S22-V1"
    const val SANITIZED_SHA256_PATTERN = "^[0-9a-f]{64}$"
    const val MAX_REQUEST_BYTES = 256 * 1024
    const val MAX_RESPONSE_BYTES = 1024 * 1024
    const val MAX_POSITIONS = 1_000
    const val MAX_VIOLATIONS = 14
    const val MAX_ISSUES = 14
    const val MAX_WARNINGS = 50
    const val MAX_ABSTENTIONS = 50
    const val MAX_DISCLOSURE_EVENTS = 100
    const val MAX_SOURCE_REFS = 100
    const val MAX_ID_OR_CODE_CHARS = 128
    const val MAX_SAFE_MESSAGE_CHARS = 1_024
    const val MAX_LOGICAL_CALLS_PER_PORT = 1
    const val MAX_CONCURRENCY = 8
    val SOURCE_DEADLINE: Duration = Duration.ofMillis(500)
    val EVALUATION_DEADLINE: Duration = Duration.ofMillis(900)
}
