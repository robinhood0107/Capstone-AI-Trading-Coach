package com.capstone.decision.api.rag

import com.capstone.decision.application.rag.RagFieldViolation
import com.capstone.decision.application.rag.RagValidationException
import jakarta.servlet.http.HttpServletRequest
import org.springframework.stereotype.Component
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.HexFormat

@Component
class RagRequestParser {
    /**
     * `/rag/sources`는 cursor/query/filter CRUD 표면을 열지 않는 고정 metadata 목록이다.
     * 검색어, sourceTier, profile 같은 제어 입력은 S4.3 이후 별도 계약으로만 추가한다.
     */
    fun requireNoQuery(request: HttpServletRequest) {
        val violations =
            request.parameterMap.keys
                .sorted()
                .take(MAX_QUERY_VIOLATIONS)
                .map { name -> RagFieldViolation(boundedQueryPointer(name), "UNKNOWN_FIELD") }
        if (violations.isNotEmpty()) {
            throw RagValidationException(violations)
        }
    }

    private fun escapePointer(value: String): String =
        value
            .replace("~", "~0")
            .replace("/", "~1")

    private fun boundedQueryPointer(name: String): String {
        val pointer = "/query/${escapePointer(name)}"
        if (pointer.length <= MAX_FIELD_LENGTH) {
            return pointer
        }
        // 긴 attacker-controlled 이름은 절단 충돌 대신 고정 SHA-256 sentinel로 응답 schema 상한에 맞춘다.
        val digest =
            HexFormat
                .of()
                .formatHex(MessageDigest.getInstance("SHA-256").digest(name.toByteArray(StandardCharsets.UTF_8)))
        return "/query/__name_sha256_$digest"
    }

    private companion object {
        const val MAX_QUERY_VIOLATIONS = 64
        const val MAX_FIELD_LENGTH = 512
    }
}
