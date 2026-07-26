package com.capstone.decision.api.brokerage

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ApiWarning
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.brokerage.BrokerageActor
import com.capstone.decision.application.brokerage.paper.PaperBalanceProjection
import com.capstone.decision.application.brokerage.paper.PaperBrokerageService
import com.capstone.decision.application.brokerage.paper.PaperBuyableProjection
import com.capstone.decision.application.brokerage.paper.PaperOrderProjection
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

@RestController
@RequestMapping("/api/v1/brokerage", produces = [MediaType.APPLICATION_JSON_VALUE])
class PaperBrokerageController(
    private val service: PaperBrokerageService,
    private val parser: BrokerageRequestParser,
) {
    @Operation(
        summary = "저장된 sanitized 시세만 사용해 INTERNAL_PAPER 주문을 원자 체결한다.",
        requestBody =
            OasRequestBody(
                required = true,
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S32PaperOrderRequest"))],
            ),
        responses = [
            OasApiResponse(
                responseCode = "200",
                description = "Filled or accepted paper order.",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S32PaperOrderSuccessResponse"))],
            ),
            OasApiResponse(responseCode = "400", description = "Invalid body or unverified LIMIT tick table."),
            OasApiResponse(responseCode = "401", description = "Authentication is required."),
            OasApiResponse(responseCode = "404", description = "Owned Decision or paper account was not found."),
            OasApiResponse(responseCode = "409", description = "Decision/idempotency conflict or stale price."),
            OasApiResponse(responseCode = "422", description = "Risk controls block order submission."),
            OasApiResponse(responseCode = "503", description = "Stored paper source is unavailable."),
        ],
    )
    @PostMapping("/paper/orders", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun submitPaperOrder(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Parameter(
            description = "16-128 ASCII characters. Raw value is never persisted.",
            required = true,
            schema = OasSchema(pattern = "^[A-Za-z0-9._:-]{16,128}$"),
        )
        @RequestHeader(name = "X-Idempotency-Key", required = false) idempotencyKey: String?,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ApiResponse<PaperOrderProjection> {
        parser.requireNoQuery(request)
        val rawKey = parser.requireIdempotencyKey(idempotencyKey)
        val command = parser.parseSubmit(body.orEmpty())
        val requestId = RequestIds.currentOrCreate(request)
        val projection =
            service.submitPaperOrder(
                actor =
                    BrokerageActor(
                        userId = principal.userId,
                        role = principal.role,
                        securityVersion = principal.securityVersion,
                        requestId = requestId,
                    ),
                rawIdempotencyKey = rawKey,
                command = command,
            )
        return ApiResponseFactory.success(
            requestId = requestId,
            data = projection,
            warnings =
                if (projection.status == "ACCEPTED") {
                    listOf(
                        ApiWarning(
                            code = "PAPER_LIMIT_NOT_FILLED",
                            message = "Paper LIMIT condition was not met; no ledger mutation occurred.",
                        ),
                    )
                } else {
                    emptyList()
                },
        )
    }

    @Operation(
        summary = "Owner-scoped INTERNAL_PAPER 현금·포지션 파생 상태를 조회한다.",
        responses = [
            OasApiResponse(
                responseCode = "200",
                description = "Paper balance projection.",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S32PaperBalanceSuccessResponse"))],
            ),
            OasApiResponse(responseCode = "401", description = "Authentication is required."),
            OasApiResponse(responseCode = "404", description = "Owned paper account was not found."),
        ],
    )
    @GetMapping("/paper/accounts/{accountId}/balances")
    fun getPaperBalance(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Parameter(schema = OasSchema(pattern = "^acct_[0-9a-f]{32}$"))
        @PathVariable accountId: String,
        request: HttpServletRequest,
    ): ApiResponse<PaperBalanceProjection> {
        parser.requireNoQuery(request)
        return ApiResponseFactory.success(
            requestId = RequestIds.currentOrCreate(request),
            data = service.getOwnedBalance(principal.userId, parser.parseAccountId(accountId)),
        )
    }

    @Operation(
        summary = "Owner-scoped INTERNAL_PAPER 현금으로 주문가능 수량을 계산한다.",
        responses = [
            OasApiResponse(
                responseCode = "200",
                description = "Paper buyable projection.",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S32PaperBuyableSuccessResponse"))],
            ),
            OasApiResponse(responseCode = "400", description = "Invalid path or query."),
            OasApiResponse(responseCode = "401", description = "Authentication is required."),
            OasApiResponse(responseCode = "404", description = "Owned paper account was not found."),
        ],
    )
    @GetMapping("/paper/accounts/{accountId}/buyable")
    fun getPaperBuyable(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Parameter(schema = OasSchema(pattern = "^acct_[0-9a-f]{32}$"))
        @PathVariable accountId: String,
        request: HttpServletRequest,
    ): ApiResponse<PaperBuyableProjection> {
        val query = parser.parseBuyableQuery(request)
        return ApiResponseFactory.success(
            requestId = RequestIds.currentOrCreate(request),
            data =
                service.getOwnedBuyable(
                    actorUserId = principal.userId,
                    accountId = parser.parseAccountId(accountId),
                    symbol = query.symbol,
                    estimatedPrice = query.price,
                ),
        )
    }
}
