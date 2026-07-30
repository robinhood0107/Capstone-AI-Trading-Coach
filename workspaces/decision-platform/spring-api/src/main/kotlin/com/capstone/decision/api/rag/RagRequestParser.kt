package com.capstone.decision.api.rag

import com.capstone.decision.application.rag.RagFieldViolation
import com.capstone.decision.application.rag.RagValidationException
import jakarta.servlet.http.HttpServletRequest
import org.springframework.stereotype.Component

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
                .map { name -> RagFieldViolation("/query/${escapePointer(name)}", "UNKNOWN_FIELD") }
        if (violations.isNotEmpty()) {
            throw RagValidationException(violations)
        }
    }

    private fun escapePointer(value: String): String =
        value
            .replace("~", "~0")
            .replace("/", "~1")
}
