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
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestHeader
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import io.swagger.v3.oas.annotations.parameters.RequestBody as OpenApiRequestBody
import io.swagger.v3.oas.annotations.responses.ApiResponse as OpenApiResponse

@RestController
@RequestMapping("/api/v1/automation", produces = [MediaType.APPLICATION_JSON_VALUE])
class AutomationController(
    private val service: AutomationService,
    private val parser: AutomationRequestParser,
) {
    @Operation(operationId = "getAutomationStatus", summary = "Owner automation status / 자동운용 상태")
    @CommonAutomationResponses
    @GetMapping("/status")
    fun status(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<AutomationControlResponse> {
        parser.requireNoQuery(request)
        return ApiResponseFactory.success(RequestIds.currentOrCreate(request), service.status(principal.userId).toResponse())
    }

    @Operation(operationId = "armAutomation", summary = "Arm owner automation / 자동운용 활성화")
    @OpenApiRequestBody(required = true, content = [Content(schema = Schema(implementation = ArmAutomationRequestSchema::class))])
    @CommonAutomationResponses
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
    ): ApiResponse<AutomationControlResponse> {
        parser.requireNoQuery(request)
        val key = parser.requireIdempotencyKey(idempotencyKey)
        val result = service.arm(principal.userId, key, parser.parseArm(body.orEmpty()))
        return ApiResponseFactory.success(RequestIds.currentOrCreate(request), result.toResponse())
    }

    @Operation(operationId = "disarmAutomation", summary = "Disarm owner automation / 자동운용 비활성화")
    @OpenApiRequestBody(required = true, content = [Content(schema = Schema(implementation = DisarmAutomationRequestSchema::class))])
    @CommonAutomationResponses
    @PostMapping("/disarm", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun disarm(
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
    ): ApiResponse<AutomationControlResponse> {
        parser.requireNoQuery(request)
        val key = parser.requireIdempotencyKey(idempotencyKey)
        val result = service.disarm(principal.userId, key, parser.parseDisarm(body.orEmpty()))
        return ApiResponseFactory.success(RequestIds.currentOrCreate(request), result.toResponse())
    }

    @Operation(
        operationId = "listAutomationRuns",
        summary = "List owner automation runs / 자동운용 실행 목록",
        parameters = [
            Parameter(name = "size", `in` = ParameterIn.QUERY, schema = Schema(type = "integer", minimum = "1", maximum = "100")),
            Parameter(name = "cursor", `in` = ParameterIn.QUERY, schema = Schema(type = "string", maxLength = 512)),
        ],
    )
    @CommonAutomationResponses
    @GetMapping("/runs")
    fun runs(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<AutomationRunPageResponse> {
        val query = parser.parseRunsQuery(request)
        val page = service.listRuns(principal.userId, query.size, query.cursor)
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            AutomationRunPageResponse(page.items.map { it.toResponse() }, page.nextCursor),
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
            content = [Content(schema = Schema(implementation = P1AutomationErrorResponseSchema::class))],
        ),
        OpenApiResponse(
            responseCode = "401",
            description = "Authentication required",
            content = [Content(schema = Schema(implementation = P1AutomationErrorResponseSchema::class))],
        ),
        OpenApiResponse(
            responseCode = "404",
            description = "Missing or cross-owner",
            content = [Content(schema = Schema(implementation = P1AutomationErrorResponseSchema::class))],
        ),
        OpenApiResponse(
            responseCode = "409",
            description = "Version, gate, or idempotency conflict",
            content = [Content(schema = Schema(implementation = P1AutomationErrorResponseSchema::class))],
        ),
    ],
)
annotation class CommonAutomationResponses
