package com.capstone.decision.api.strongllm

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.application.strongllm.AiReviewStatus
import com.capstone.decision.application.strongllm.AiReviewStatusService
import io.swagger.v3.oas.annotations.Operation
import jakarta.servlet.http.HttpServletRequest
import org.springframework.context.annotation.Profile
import org.springframework.http.MediaType
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

/**
 * "내 Vertex 키" 상태: 자기 키 등록 여부, AI 검토가 어느 키로 불릴지, 자기 사용량. 호출자 본인 것만 읽는다.
 * 키·암호문은 어떤 응답에도 없다.
 */
@RestController
@Profile("mars-full")
@RequestMapping("/api/v1/ai-review", produces = [MediaType.APPLICATION_JSON_VALUE])
class AiReviewStatusController(
    private val service: AiReviewStatusService,
) {
    @Operation(operationId = "readOwnAiReviewStatus")
    @GetMapping("/status")
    fun status(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<AiReviewStatus> {
        if (request.queryString != null) throw ApiException(ErrorCode.VALIDATION_ERROR)
        return ApiResponseFactory.success(RequestIds.currentOrCreate(request), service.status(principal.userId))
    }
}
