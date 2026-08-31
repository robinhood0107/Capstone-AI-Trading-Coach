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
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestHeader
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

/** Runtime routes use the reviewed exact-75 overlay instead of springdoc's inferred schemas. */
@Hidden
@RestController
@RequestMapping("/api/v3/automation", produces = [MediaType.APPLICATION_JSON_VALUE])
class AutomationV3Controller(
    private val service: AutomationService,
    private val parser: AutomationRequestParser,
) {
    @GetMapping("/status")
    fun status(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<AutomationStatusV3Response> {
        parser.requireNoQuery(request)
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            service.statusV3(principal.userId).toV3Response(),
        )
    }

    @PutMapping("/policy", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun putPolicy(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestHeader(name = "X-Idempotency-Key", required = false) idempotencyKey: String?,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ApiResponse<AutomationPolicyV3Response> {
        parser.requireNoQuery(request)
        val result =
            service.putPolicyV3(
                principal.userId,
                parser.requireIdempotencyKey(idempotencyKey),
                parser.parsePutPolicyV3(body.orEmpty()),
            )
        return ApiResponseFactory.success(RequestIds.currentOrCreate(request), result.toV3Response())
    }

    @PostMapping("/arm", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun arm(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestHeader(name = "X-Idempotency-Key", required = false) idempotencyKey: String?,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ApiResponse<AutomationStatusV3Response> {
        parser.requireNoQuery(request)
        val result =
            service.armV3(
                principal.userId,
                parser.requireIdempotencyKey(idempotencyKey),
                parser.parseArmV3(body.orEmpty()),
            )
        return ApiResponseFactory.success(RequestIds.currentOrCreate(request), result.toV3Response())
    }

    @GetMapping("/runs")
    fun runs(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<AutomationRunPageV3Response> {
        val query = parser.parseRunsQuery(request)
        val page = service.listRunsV3(principal.userId, query.size, query.cursor)
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            AutomationRunPageV3Response(page.items.map { it.toV3Response() }, page.nextCursor),
        )
    }

    @GetMapping("/runs/{runId}")
    fun run(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable runId: String,
        request: HttpServletRequest,
    ): ApiResponse<AutomationRunDetailV3Response> {
        parser.requireNoQuery(request)
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            service.readRunV3(principal.userId, runId).toV3Response(),
        )
    }

    @GetMapping("/positions")
    fun positions(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<AutomationPositionPageV3Response> {
        parser.requireNoQuery(request)
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            AutomationPositionPageV3Response(
                service.listPositionsV3(principal.userId).map { it.toV3Response() },
            ),
        )
    }
}
