package com.capstone.decision.api.decision

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.decision.DecisionActor
import com.capstone.decision.application.decision.DecisionAuditProjection
import com.capstone.decision.application.decision.DecisionProjection
import com.capstone.decision.application.decision.DecisionService
import com.capstone.decision.application.security.AppPrincipal
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.MediaType
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestHeader
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

/**
 * JWT subject만 actor로 사용하며 body의 user/account/mode/corpCode/time 주입을 parser 단계에서 거부한다.
 */
@RestController
@RequestMapping("/api/v1/decisions", produces = [MediaType.APPLICATION_JSON_VALUE])
class DecisionController(
    private val service: DecisionService,
    private val parser: DecisionRequestParser,
) {
    @PostMapping("/evaluate-order", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun evaluateOrder(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestHeader(name = "X-Idempotency-Key", required = false) idempotencyKey: String?,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ApiResponse<DecisionProjection> {
        parser.requireNoQuery(request)
        val rawKey = parser.requireIdempotencyKey(idempotencyKey)
        val command = parser.parseEvaluate(body.orEmpty())
        val requestId = RequestIds.currentOrCreate(request)
        return ApiResponseFactory.success(
            requestId = requestId,
            data =
                service.evaluate(
                    actor =
                        DecisionActor(
                            userId = principal.userId,
                            role = principal.role,
                            requestId = requestId,
                        ),
                    rawIdempotencyKey = rawKey,
                    command = command,
                ),
        )
    }

    @GetMapping("/{decisionId}")
    fun getDecision(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable decisionId: String,
        request: HttpServletRequest,
    ): ApiResponse<DecisionProjection> {
        parser.requireNoQuery(request)
        val parsedId = parser.parseDecisionId(decisionId)
        return ApiResponseFactory.success(
            requestId = RequestIds.currentOrCreate(request),
            data = service.getOwned(principal.userId, parsedId),
        )
    }

    @GetMapping("/{decisionId}/audit")
    fun getDecisionAudit(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable decisionId: String,
        request: HttpServletRequest,
    ): ApiResponse<DecisionAuditProjection> {
        parser.requireNoQuery(request)
        val parsedId = parser.parseDecisionId(decisionId)
        return ApiResponseFactory.success(
            requestId = RequestIds.currentOrCreate(request),
            data = service.getOwnedAudit(principal.userId, parsedId),
        )
    }
}
