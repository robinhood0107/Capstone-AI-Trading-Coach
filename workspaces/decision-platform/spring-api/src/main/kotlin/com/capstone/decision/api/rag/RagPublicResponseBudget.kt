package com.capstone.decision.api.rag

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.application.rag.RagGuardHistoryUnavailableException
import org.springframework.stereotype.Component
import tools.jackson.databind.ObjectMapper

@Component
class RagPublicResponseBudget(
    private val objectMapper: ObjectMapper,
) {
    /**
     * JSON escaping과 공통 envelope까지 포함한 최종 RAG 응답이 exact 32KiB를 넘으면 plaintext를 반환하지 않는다.
     */
    fun <T> requireWithin(response: ApiResponse<T>): ApiResponse<T> =
        try {
            if (objectMapper.writeValueAsBytes(response).size > MAX_RESPONSE_BYTES) {
                throw RagGuardHistoryUnavailableException()
            }
            response
        } catch (exception: RagGuardHistoryUnavailableException) {
            throw exception
        } catch (exception: RuntimeException) {
            throw RagGuardHistoryUnavailableException(exception)
        }

    private companion object {
        const val MAX_RESPONSE_BYTES = 32 * 1_024
    }
}
