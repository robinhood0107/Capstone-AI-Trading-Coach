package com.capstone.decision.api.risk

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ApiWarning
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.risk.KillSwitchActor
import com.capstone.decision.application.risk.KillSwitchService
import com.capstone.decision.application.risk.PortfolioRiskQueryUseCase
import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.application.security.IdempotencyKeyPolicy
import com.capstone.decision.domain.risk.KillSwitchActorRole
import io.swagger.v3.oas.annotations.Operation
import io.swagger.v3.oas.annotations.Parameter
import io.swagger.v3.oas.annotations.media.Content
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.MediaType
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestHeader
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import io.swagger.v3.oas.annotations.media.Schema as OasSchema
import io.swagger.v3.oas.annotations.parameters.RequestBody as OasRequestBody
import io.swagger.v3.oas.annotations.responses.ApiResponse as OasApiResponse

@RestController
@RequestMapping("/api/v1/risk", produces = [MediaType.APPLICATION_JSON_VALUE])
class RiskController(
    private val killSwitchService: KillSwitchService,
    private val portfolioRiskQuery: PortfolioRiskQueryUseCase,
    private val parser: RiskRequestParser,
) {
    @Operation(
        summary = "현재 owner-scoped portfolio 리스크를 stored observation으로 조회한다.",
        responses = [
            OasApiResponse(
                responseCode = "200",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S24PortfolioRiskSuccessResponse"))],
            ),
            OasApiResponse(
                responseCode = "401",
                description = "Authentication is required.",
                content = [Content(schema = OasSchema(implementation = S24RiskErrorResponseSchema::class))],
            ),
            OasApiResponse(
                responseCode = "503",
                description = "Risk authority is unavailable.",
                content = [Content(schema = OasSchema(implementation = S24RiskErrorResponseSchema::class))],
            ),
        ],
    )
    @GetMapping("/portfolio")
    fun getPortfolio(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<PortfolioRiskDto> {
        parser.requireNoQuery(request)
        val result = portfolioRiskQuery.get(principal.userId)
        return ApiResponseFactory.success(
            requestId = RequestIds.currentOrCreate(request),
            data = result.projection.toDto(),
            warnings =
                result.warnings.map { warning ->
                    ApiWarning(
                        code = warning.code,
                        message = "One or more portfolio risk sources are unavailable.",
                        details = mapOf("fields" to warning.fields),
                    )
                },
        )
    }

    @Operation(
        summary = "현재 GLOBAL Kill Switch의 sanitized 상태를 DB에서 직접 조회한다.",
        responses = [
            OasApiResponse(
                responseCode = "200",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S24KillSwitchSuccessResponse"))],
            ),
            OasApiResponse(
                responseCode = "401",
                description = "Authentication is required.",
                content = [Content(schema = OasSchema(implementation = S24RiskErrorResponseSchema::class))],
            ),
            OasApiResponse(
                responseCode = "503",
                description = "Risk authority is unavailable.",
                content = [Content(schema = OasSchema(implementation = S24RiskErrorResponseSchema::class))],
            ),
        ],
    )
    @GetMapping("/kill-switch")
    fun getKillSwitch(request: HttpServletRequest): ApiResponse<KillSwitchStateDto> {
        parser.requireNoQuery(request)
        return ApiResponseFactory.success(
            requestId = RequestIds.currentOrCreate(request),
            data = killSwitchService.getState().toDto(),
        )
    }

    @Operation(
        summary = "Kill Switch를 변경한다. USER는 활성화만, ADMIN은 활성화와 해제가 가능하다.",
        requestBody =
            OasRequestBody(
                required = true,
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S24KillSwitchRequest"))],
            ),
        responses = [
            OasApiResponse(
                responseCode = "200",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S24KillSwitchSuccessResponse"))],
            ),
            OasApiResponse(
                responseCode = "400",
                description = "Invalid body or idempotency key.",
                content = [Content(schema = OasSchema(implementation = S24RiskErrorResponseSchema::class))],
            ),
            OasApiResponse(
                responseCode = "401",
                description = "Authentication is required.",
                content = [Content(schema = OasSchema(implementation = S24RiskErrorResponseSchema::class))],
            ),
            OasApiResponse(
                responseCode = "403",
                description = "Only a current ADMIN can resume.",
                content = [Content(schema = OasSchema(implementation = S24RiskErrorResponseSchema::class))],
            ),
            OasApiResponse(
                responseCode = "409",
                description = "Concurrent generation conflict.",
                content = [Content(schema = OasSchema(implementation = S24RiskErrorResponseSchema::class))],
            ),
            OasApiResponse(
                responseCode = "503",
                description = "Risk authority is unavailable.",
                content = [Content(schema = OasSchema(implementation = S24RiskErrorResponseSchema::class))],
            ),
        ],
    )
    @PostMapping("/kill-switch", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun changeKillSwitch(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Parameter(
            description = "16-128 bounded ASCII identifier. Raw value is handled only by the idempotency filter.",
            required = true,
            schema =
                OasSchema(
                    type = "string",
                    minLength = IdempotencyKeyPolicy.MIN_LENGTH,
                    maxLength = IdempotencyKeyPolicy.MAX_LENGTH,
                    pattern = IdempotencyKeyPolicy.PATTERN,
                ),
        )
        @RequestHeader(name = "X-Idempotency-Key", required = false) idempotencyKey: String?,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ApiResponse<KillSwitchStateDto> {
        parser.requireNoQuery(request)
        parser.requireIdempotencyKey(idempotencyKey)
        val parsed = parser.parseKillSwitchChange(body.orEmpty())
        val requestId = RequestIds.currentOrCreate(request)
        val result =
            killSwitchService.change(
                actor =
                    KillSwitchActor(
                        userId = principal.userId,
                        role =
                            runCatching { KillSwitchActorRole.valueOf(principal.role) }.getOrElse {
                                throw com.capstone.decision.application.risk
                                    .KillSwitchForbiddenException()
                            },
                        securityVersion = principal.securityVersion,
                        requestId = requestId,
                    ),
                active = parsed.active,
                rawReason = parsed.reason,
            )
        return ApiResponseFactory.success(
            requestId = requestId,
            data = result.state.toDto(),
        )
    }
}
