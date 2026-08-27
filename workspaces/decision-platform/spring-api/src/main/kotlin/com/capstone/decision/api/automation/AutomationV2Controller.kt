package com.capstone.decision.api.automation

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.automation.AutomationService
import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.application.security.IdempotencyKeyPolicy
import io.swagger.v3.oas.annotations.Operation
import io.swagger.v3.oas.annotations.Parameter
import io.swagger.v3.oas.annotations.enums.ParameterIn
import io.swagger.v3.oas.annotations.media.Content
import io.swagger.v3.oas.annotations.media.Schema
import io.swagger.v3.oas.annotations.responses.ApiResponses
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.MediaType
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestHeader
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import io.swagger.v3.oas.annotations.parameters.RequestBody as OpenApiRequestBody
import io.swagger.v3.oas.annotations.responses.ApiResponse as OpenApiResponse

@RestController
@RequestMapping("/api/v2/automation", produces = [MediaType.APPLICATION_JSON_VALUE])
class AutomationV2Controller(
    private val service: AutomationService,
    private val parser: AutomationRequestParser,
) {
    @Operation(operationId = "getAutomationStatusV2", summary = "Variable automation status / 가변수량 자동운용 상태")
    @CommonAutomationV2Responses
    @GetMapping("/status")
    fun status(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<AutomationStatusV2Response> {
        parser.requireNoQuery(request)
        return ApiResponseFactory.success(RequestIds.currentOrCreate(request), service.statusV2(principal.userId).toResponse())
    }

    @Operation(operationId = "putAutomationPolicyV2", summary = "Create or update variable automation policy")
    @OpenApiRequestBody(required = true, content = [Content(schema = Schema(implementation = PutAutomationPolicyV2RequestSchema::class))])
    @CommonAutomationV2Responses
    @PutMapping("/policy", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun putPolicy(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Parameter(
            required = true,
            schema =
                Schema(
                    minLength = IdempotencyKeyPolicy.MIN_LENGTH,
                    maxLength = IdempotencyKeyPolicy.MAX_LENGTH,
                    pattern = IdempotencyKeyPolicy.PATTERN,
                ),
        )
        @RequestHeader(name = "X-Idempotency-Key", required = false) idempotencyKey: String?,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ApiResponse<AutomationPolicyV2Response> {
        parser.requireNoQuery(request)
        val key = parser.requireIdempotencyKey(idempotencyKey)
        val result = service.putPolicyV2(principal.userId, key, parser.parsePutPolicyV2(body.orEmpty()))
        return ApiResponseFactory.success(RequestIds.currentOrCreate(request), result.toResponse())
    }

    @Operation(operationId = "armAutomationV2", summary = "Arm variable KIS mock automation")
    @OpenApiRequestBody(required = true, content = [Content(schema = Schema(implementation = ArmAutomationV2RequestSchema::class))])
    @CommonAutomationV2Responses
    @PostMapping("/arm", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun arm(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Parameter(
            required = true,
            schema =
                Schema(
                    minLength = IdempotencyKeyPolicy.MIN_LENGTH,
                    maxLength = IdempotencyKeyPolicy.MAX_LENGTH,
                    pattern = IdempotencyKeyPolicy.PATTERN,
                ),
        )
        @RequestHeader(name = "X-Idempotency-Key", required = false) idempotencyKey: String?,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ApiResponse<AutomationStatusV2Response> {
        parser.requireNoQuery(request)
        val key = parser.requireIdempotencyKey(idempotencyKey)
        val result = service.armV2(principal.userId, key, parser.parseArmV2(body.orEmpty()))
        return ApiResponseFactory.success(RequestIds.currentOrCreate(request), result.toResponse())
    }

    @Operation(
        operationId = "listAutomationRunsV2",
        summary = "List variable automation runs",
        parameters = [
            Parameter(name = "size", `in` = ParameterIn.QUERY, schema = Schema(type = "integer", minimum = "1", maximum = "100")),
            Parameter(name = "cursor", `in` = ParameterIn.QUERY, schema = Schema(type = "string", maxLength = 512)),
        ],
    )
    @CommonAutomationV2Responses
    @GetMapping("/runs")
    fun runs(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<AutomationRunPageV2Response> {
        val query = parser.parseRunsQuery(request)
        val page = service.listRunsV2(principal.userId, query.size, query.cursor)
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            AutomationRunPageV2Response(page.items.map { it.toResponse() }, page.nextCursor),
        )
    }

    @Operation(operationId = "listAutomationPositionsV2", summary = "List active bot-owned variable positions")
    @CommonAutomationV2Responses
    @GetMapping("/positions")
    fun positions(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<AutomationPositionPageV2Response> {
        parser.requireNoQuery(request)
        val page = service.listPositionsV2(principal.userId)
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            AutomationPositionPageV2Response(page.items.map { it.toResponse() }, page.nextCursor),
        )
    }
}

@Target(AnnotationTarget.FUNCTION)
@Retention(AnnotationRetention.RUNTIME)
@ApiResponses(
    value = [
        OpenApiResponse(responseCode = "200", description = "Success"),
        OpenApiResponse(
            responseCode = "400",
            description = "Validation error",
            content = [Content(schema = Schema(implementation = P1AutomationV2ErrorResponseSchema::class))],
        ),
        OpenApiResponse(
            responseCode = "401",
            description = "Authentication required",
            content = [Content(schema = Schema(implementation = P1AutomationV2ErrorResponseSchema::class))],
        ),
        OpenApiResponse(
            responseCode = "404",
            description = "Missing or cross-owner",
            content = [Content(schema = Schema(implementation = P1AutomationV2ErrorResponseSchema::class))],
        ),
        OpenApiResponse(
            responseCode = "409",
            description = "Version, readiness, or idempotency conflict",
            content = [Content(schema = Schema(implementation = P1AutomationV2ErrorResponseSchema::class))],
        ),
    ],
)
annotation class CommonAutomationV2Responses
