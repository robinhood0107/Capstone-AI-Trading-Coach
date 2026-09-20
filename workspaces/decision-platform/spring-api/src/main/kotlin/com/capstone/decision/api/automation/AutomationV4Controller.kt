package com.capstone.decision.api.automation

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.automation.AutomationService
import com.capstone.decision.application.security.AppPrincipal
import io.swagger.v3.oas.annotations.Hidden
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.MediaType
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestHeader
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

/** 다중 주문 자체와 분리된 다음 세션 자본정책 API다. 진행 중 run의 snapshot은 바꾸지 않는다. */
@Hidden
@RestController
@RequestMapping("/api/v4/automation", produces = [MediaType.APPLICATION_JSON_VALUE])
class AutomationV4Controller(
    private val service: AutomationService,
    private val parser: AutomationRequestParser,
) {
    @GetMapping("/capital-policy")
    fun capitalPolicy(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<AutomationCapitalPolicyResponse?> {
        parser.requireNoQuery(request)
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            service.capitalPolicy(principal.userId)?.toCapitalPolicyResponse(),
        )
    }

    @PutMapping("/capital-policy", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun putCapitalPolicy(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestHeader(name = "X-Idempotency-Key", required = false) idempotencyKey: String?,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ApiResponse<AutomationCapitalPolicyResponse> {
        parser.requireNoQuery(request)
        val result =
            service.putCapitalPolicy(
                principal.userId,
                parser.requireIdempotencyKey(idempotencyKey),
                parser.parsePutCapitalPolicy(body.orEmpty()),
            )
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            result.toCapitalPolicyResponse(),
        )
    }

    @GetMapping("/capital-status")
    fun capitalStatus(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<AutomationCapitalStatusResponse?> {
        parser.requireNoQuery(request)
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            service.capitalStatus(principal.userId)?.toCapitalStatusResponse(),
        )
    }
}
