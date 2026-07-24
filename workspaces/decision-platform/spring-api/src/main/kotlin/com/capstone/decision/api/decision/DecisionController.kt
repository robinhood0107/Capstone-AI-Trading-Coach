package com.capstone.decision.api.decision

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.decision.DecisionActor
import com.capstone.decision.application.decision.DecisionAuditProjection
import com.capstone.decision.application.decision.DecisionProjection
import com.capstone.decision.application.decision.DecisionService
import com.capstone.decision.application.security.AppPrincipal
import io.swagger.v3.oas.annotations.Operation
import io.swagger.v3.oas.annotations.Parameter
import io.swagger.v3.oas.annotations.media.Content
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
import io.swagger.v3.oas.annotations.media.Schema as OasSchema
import io.swagger.v3.oas.annotations.parameters.RequestBody as OasRequestBody
import io.swagger.v3.oas.annotations.responses.ApiResponse as OasApiResponse

/**
 * JWT subject만 actor로 사용하며 body의 user/account/mode/corpCode/time 주입을 parser 단계에서 거부한다.
 */
@RestController
@RequestMapping("/api/v1/decisions", produces = [MediaType.APPLICATION_JSON_VALUE])
class DecisionController(
    private val service: DecisionService,
    private val parser: DecisionRequestParser,
) {
    @Operation(
        summary = "저장 observation으로 주문 의도를 평가하고 Decision을 원자 저장한다.",
        requestBody =
            OasRequestBody(
                required = true,
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S23EvaluateOrderRequest"))],
            ),
        responses = [
            OasApiResponse(
                responseCode = "200",
                description = "Canonical persisted Decision. Expected source absence is a HOLD.",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S23DecisionSuccessResponse"))],
            ),
            OasApiResponse(responseCode = "400", description = "Invalid selector or body."),
            OasApiResponse(responseCode = "401", description = "Authentication is required."),
            OasApiResponse(responseCode = "404", description = "Owned ACTIVE Principle was not found."),
            OasApiResponse(responseCode = "409", description = "Idempotency or pinned-version conflict."),
            OasApiResponse(responseCode = "413", description = "Request payload exceeds 262144 bytes."),
            OasApiResponse(responseCode = "500", description = "Technical fail-closed error with no Decision side effect."),
        ],
    )
    @PostMapping("/evaluate-order", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun evaluateOrder(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Parameter(
            description = "16-128 ASCII characters. Raw value is never stored, logged, or used as a metric tag.",
            required = true,
            schema = OasSchema(pattern = "^[A-Za-z0-9._:-]{16,128}$"),
        )
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

    @Operation(
        summary = "JWT owner에게 canonical persisted Decision을 반환한다.",
        responses = [
            OasApiResponse(
                responseCode = "200",
                description = "The same canonical projection returned by evaluate-order.",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S23DecisionSuccessResponse"))],
            ),
            OasApiResponse(responseCode = "401", description = "Authentication is required."),
            OasApiResponse(responseCode = "404", description = "Owned Decision was not found."),
        ],
    )
    @GetMapping("/{decisionId}")
    fun getDecision(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Parameter(schema = OasSchema(pattern = "^dec_[0-9a-f]{32}$"))
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

    @Operation(
        summary = "JWT owner에게 reference-only sanitized Decision audit을 반환한다.",
        responses = [
            OasApiResponse(
                responseCode = "200",
                description = "Sanitized owner-scoped audit projection.",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S23DecisionAuditSuccessResponse"))],
            ),
            OasApiResponse(responseCode = "401", description = "Authentication is required."),
            OasApiResponse(responseCode = "404", description = "Owned Decision audit was not found."),
        ],
    )
    @GetMapping("/{decisionId}/audit")
    fun getDecisionAudit(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Parameter(schema = OasSchema(pattern = "^dec_[0-9a-f]{32}$"))
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
