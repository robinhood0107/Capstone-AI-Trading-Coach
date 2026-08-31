package com.capstone.decision.api.strongllm

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.strongllm.StrongLlmCredentialCorruptedException
import com.capstone.decision.application.strongllm.StrongLlmSettingsUnavailableException
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice

/**
 * 이 handler가 없으면 잘못된 설정이 401로 보인다.
 *
 * 처리되지 않은 예외는 Spring의 error dispatch를 타는데, 인증 필터는 그 dispatch에서 다시 돌지
 * 않아 SecurityContext가 비어 있고 entry point가 UNAUTHORIZED를 쓴다. 그러면 "provider 이름이
 * 틀렸다"가 "다시 로그인하세요"로 나온다. 원인을 감추는 실패다.
 *
 * 어느 필드가 틀렸는지는 담지 않는다. 이 요청의 본문에는 API 키가 들어 있고, 필드 이름을
 * 되비추기 시작하면 언젠가 값도 함께 나간다.
 */
@RestControllerAdvice(assignableTypes = [StrongLlmSettingsController::class])
class StrongLlmSettingsExceptionHandler {
    @ExceptionHandler(IllegalArgumentException::class)
    fun invalid(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.VALIDATION_ERROR)

    @ExceptionHandler(StrongLlmCredentialCorruptedException::class)
    fun corrupted(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.INTERNAL_ERROR)

    @ExceptionHandler(StrongLlmSettingsUnavailableException::class)
    fun unavailable(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.INTERNAL_ERROR)

    private fun error(
        request: HttpServletRequest,
        code: ErrorCode,
    ): ResponseEntity<ApiResponse<Nothing>> =
        ResponseEntity.status(code.status).body(ApiResponseFactory.error(RequestIds.currentOrCreate(request), code))
}
