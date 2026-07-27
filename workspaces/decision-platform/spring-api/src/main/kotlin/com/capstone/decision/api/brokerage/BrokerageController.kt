package com.capstone.decision.api.brokerage

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.brokerage.BrokerageActor
import com.capstone.decision.application.brokerage.BrokerageService
import com.capstone.decision.application.brokerage.MockBalanceProjection
import com.capstone.decision.application.brokerage.MockBuyableProjection
import com.capstone.decision.application.brokerage.MockOrderProjection
import com.capstone.decision.application.brokerage.OrderDetailProjection
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
class BrokerageController(
    private val service: BrokerageService,
    private val parser: BrokerageRequestParser,
) {
    @Operation(
        summary = "KIS Mock 주문을 Decision 기반으로 제출하고 mock ledger에 원자 저장한다.",
        requestBody =
            OasRequestBody(
                required = true,
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S31MockOrderRequest"))],
            ),
        responses = [
            OasApiResponse(
                responseCode = "200",
                description = "Submitted mock order projection.",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S31MockOrderSuccessResponse"))],
            ),
            OasApiResponse(responseCode = "400", description = "Invalid body or idempotency key."),
            OasApiResponse(responseCode = "401", description = "Authentication is required."),
            OasApiResponse(responseCode = "404", description = "Owned Decision was not found."),
            OasApiResponse(responseCode = "409", description = "Decision expired, consumed, or idempotency conflict."),
            OasApiResponse(responseCode = "422", description = "Risk controls block order submission."),
            OasApiResponse(responseCode = "503", description = "Brokerage or risk gate is unavailable."),
        ],
    )
    @PostMapping("/mock/orders", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun submitMockOrder(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Parameter(
            description = "16-128 ASCII characters. Raw value is never persisted in the order ledger.",
            required = true,
            schema = OasSchema(pattern = "^[A-Za-z0-9._:-]{16,128}$"),
        )
        @RequestHeader(name = "X-Idempotency-Key", required = false) idempotencyKey: String?,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ApiResponse<MockOrderProjection> {
        parser.requireNoQuery(request)
        val rawKey = parser.requireIdempotencyKey(idempotencyKey)
        val command = parser.parseSubmit(body.orEmpty())
        val requestId = RequestIds.currentOrCreate(request)
        return ApiResponseFactory.success(
            requestId = requestId,
            data =
                service.submitMockOrder(
                    actor =
                        BrokerageActor(
                            userId = principal.userId,
                            role = principal.role,
                            securityVersion = principal.securityVersion,
                            requestId = requestId,
                        ),
                    rawIdempotencyKey = rawKey,
                    command = command,
                ),
        )
    }

    @Operation(
        summary = "JWT owner에게 sanitized mock order projection을 반환한다.",
        responses = [
            OasApiResponse(
                responseCode = "200",
                description = "Owner-scoped order projection.",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S32OrderDetailSuccessResponse"))],
            ),
            OasApiResponse(responseCode = "401", description = "Authentication is required."),
            OasApiResponse(responseCode = "404", description = "Owned order was not found."),
        ],
    )
    @GetMapping("/orders/{orderId}")
    fun getOrder(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Parameter(schema = OasSchema(pattern = "^ord_(?:mock|paper)_[0-9a-f]{32}$"))
        @PathVariable orderId: String,
        request: HttpServletRequest,
    ): ApiResponse<OrderDetailProjection> {
        parser.requireNoQuery(request)
        val parsedId = parser.parseOrderId(orderId)
        return ApiResponseFactory.success(
            requestId = RequestIds.currentOrCreate(request),
            data = service.getOwnedOrder(principal.userId, parsedId),
        )
    }

    @Operation(
        summary = "Owner-scoped mock order cancel request를 append-only event로 기록한다.",
        responses = [
            OasApiResponse(
                responseCode = "200",
                description = "Cancel requested projection.",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S32OrderDetailSuccessResponse"))],
            ),
            OasApiResponse(responseCode = "400", description = "Invalid path/body/idempotency key."),
            OasApiResponse(responseCode = "401", description = "Authentication is required."),
            OasApiResponse(responseCode = "404", description = "Owned order was not found."),
            OasApiResponse(responseCode = "409", description = "Order is not cancelable."),
            OasApiResponse(responseCode = "503", description = "Brokerage gate is unavailable."),
        ],
    )
    @PostMapping("/orders/{orderId}/cancel", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun cancelOrder(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Parameter(
            description = "16-128 ASCII characters. Global write idempotency replays cancel responses.",
            required = true,
            schema = OasSchema(pattern = "^[A-Za-z0-9._:-]{16,128}$"),
        )
        @RequestHeader(name = "X-Idempotency-Key", required = false) idempotencyKey: String?,
        @Parameter(schema = OasSchema(pattern = "^ord_(?:mock|paper)_[0-9a-f]{32}$"))
        @PathVariable orderId: String,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ApiResponse<OrderDetailProjection> {
        parser.requireNoQuery(request)
        parser.requireIdempotencyKey(idempotencyKey)
        parser.parseEmptyObject(body.orEmpty())
        val parsedId = parser.parseOrderId(orderId)
        val requestId = RequestIds.currentOrCreate(request)
        return ApiResponseFactory.success(
            requestId = requestId,
            data =
                service.cancelOwnedOrder(
                    actor =
                        BrokerageActor(
                            userId = principal.userId,
                            role = principal.role,
                            securityVersion = principal.securityVersion,
                            requestId = requestId,
                        ),
                    orderId = parsedId,
                ),
        )
    }

    @Operation(
        summary = "S2.3 stored KIS_MOCK balance observation을 owner-scoped opaque accountId로 조회한다.",
        responses = [
            OasApiResponse(
                responseCode = "200",
                description = "Stored KIS_MOCK balance projection.",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S31MockBalanceSuccessResponse"))],
            ),
            OasApiResponse(responseCode = "400", description = "Invalid path or query."),
            OasApiResponse(responseCode = "401", description = "Authentication is required."),
            OasApiResponse(responseCode = "404", description = "Owned account was not found."),
            OasApiResponse(responseCode = "503", description = "Stored balance source is unavailable."),
        ],
    )
    @GetMapping("/mock/accounts/{accountId}/balances")
    fun getMockBalance(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Parameter(schema = OasSchema(pattern = "^acct_[0-9a-f]{32}$"))
        @PathVariable accountId: String,
        request: HttpServletRequest,
    ): ApiResponse<MockBalanceProjection> {
        parser.requireNoQuery(request)
        val parsedAccountId = parser.parseAccountId(accountId)
        return ApiResponseFactory.success(
            requestId = RequestIds.currentOrCreate(request),
            data = service.getOwnedBalance(principal.userId, parsedAccountId),
        )
    }

    @Operation(
        summary = "Stored KIS_MOCK cash balance로 지정가 기준 주문가능 수량을 계산한다.",
        responses = [
            OasApiResponse(
                responseCode = "200",
                description = "Stored KIS_MOCK buyable projection.",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S31MockBuyableSuccessResponse"))],
            ),
            OasApiResponse(responseCode = "400", description = "Invalid path or query."),
            OasApiResponse(responseCode = "401", description = "Authentication is required."),
            OasApiResponse(responseCode = "404", description = "Owned account was not found."),
            OasApiResponse(responseCode = "503", description = "Stored balance source is unavailable."),
        ],
    )
    @GetMapping("/mock/accounts/{accountId}/buyable")
    fun getMockBuyable(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Parameter(schema = OasSchema(pattern = "^acct_[0-9a-f]{32}$"))
        @PathVariable accountId: String,
        request: HttpServletRequest,
    ): ApiResponse<MockBuyableProjection> {
        val parsedAccountId = parser.parseAccountId(accountId)
        val query = parser.parseBuyableQuery(request)
        return ApiResponseFactory.success(
            requestId = RequestIds.currentOrCreate(request),
            data =
                service.getOwnedBuyable(
                    actorUserId = principal.userId,
                    accountId = parsedAccountId,
                    symbol = query.symbol,
                    estimatedPrice = query.price,
                ),
        )
    }
}
