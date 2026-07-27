package com.capstone.decision.api.brokerage

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ApiWarning
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.brokerage.BrokerageActor
import com.capstone.decision.application.brokerage.BrokerageFillMode
import com.capstone.decision.application.brokerage.OrderFillApplicationService
import com.capstone.decision.application.brokerage.OrderFillPageProjection
import com.capstone.decision.application.brokerage.OrderFillQueryUseCase
import com.capstone.decision.application.brokerage.OrderFillReconciliationProjection
import com.capstone.decision.application.security.AppPrincipal
import io.swagger.v3.oas.annotations.Operation
import io.swagger.v3.oas.annotations.Parameter
import io.swagger.v3.oas.annotations.enums.ParameterIn
import io.swagger.v3.oas.annotations.media.Content
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.MediaType
import org.springframework.security.access.prepost.PreAuthorize
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
 * 공개 fill surface는 ADMIN 대사 trigger와 owner-scoped sanitized 조회뿐이며 체결 보고 route는 제공하지 않는다.
 */
@RestController
@RequestMapping("/api/v1/brokerage", produces = [MediaType.APPLICATION_JSON_VALUE])
class BrokerageFillController(
    private val applicationService: OrderFillApplicationService,
    private val queryUseCase: OrderFillQueryUseCase,
    private val brokerageParser: BrokerageRequestParser,
    private val fillParser: BrokerageFillRequestParser,
) {
    @Operation(
        summary = "저장된 체결 관측을 ADMIN 권한으로 bounded 대사한다.",
        requestBody =
            OasRequestBody(
                required = false,
                content = [
                    Content(
                        schema =
                            OasSchema(
                                type = "object",
                                maxProperties = 0,
                                additionalProperties = OasSchema.AdditionalPropertiesValue.FALSE,
                            ),
                    ),
                ],
            ),
        responses = [
            OasApiResponse(
                responseCode = "200",
                description = "Sanitized order reconciliation projection.",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S33ReconcileSuccessResponse"))],
            ),
            OasApiResponse(responseCode = "400", description = "Invalid path/body/idempotency key."),
            OasApiResponse(responseCode = "401", description = "Authentication is required."),
            OasApiResponse(responseCode = "403", description = "ADMIN role is required."),
            OasApiResponse(responseCode = "404", description = "Order was not found."),
            OasApiResponse(responseCode = "503", description = "Stored reconciliation source is unavailable."),
        ],
    )
    @PreAuthorize("hasRole('ADMIN')")
    @PostMapping("/orders/{orderId}/reconcile", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun reconcile(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Parameter(schema = OasSchema(pattern = "^ord_(?:mock|paper)_[0-9a-f]{32}$"))
        @PathVariable orderId: String,
        @Parameter(
            description = "16-128 ASCII characters. Reconciliation is globally idempotent.",
            required = true,
            schema =
                OasSchema(
                    pattern = "^[A-Za-z0-9._:-]{16,128}$",
                    minLength = 16,
                    maxLength = 128,
                ),
        )
        @RequestHeader(name = "X-Idempotency-Key", required = false) idempotencyKey: String?,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ApiResponse<OrderFillReconciliationProjection> {
        brokerageParser.requireNoQuery(request)
        brokerageParser.requireIdempotencyKey(idempotencyKey)
        brokerageParser.parseEmptyObject(body.orEmpty())
        val requestId = RequestIds.currentOrCreate(request)
        val projection =
            applicationService.reconcile(
                actor = actor(principal, requestId),
                orderId = brokerageParser.parseOrderId(orderId),
            )
        return ApiResponseFactory.success(
            requestId = requestId,
            data = projection,
            warnings =
                if (projection.reconciliation.status == "MISMATCH") {
                    listOf(
                        ApiWarning(
                            code = "ORDER_RECONCILIATION_MISMATCH",
                            message = "Stored order and fill observations require review.",
                        ),
                    )
                } else {
                    emptyList()
                },
        )
    }

    @Operation(
        summary = "Owner-scoped KIS_MOCK 체결 내역을 KST 날짜와 HMAC cursor로 조회한다.",
        parameters = [
            Parameter(
                name = "from",
                `in` = ParameterIn.QUERY,
                required = true,
                schema = OasSchema(type = "string", format = "date"),
            ),
            Parameter(
                name = "to",
                `in` = ParameterIn.QUERY,
                required = true,
                schema = OasSchema(type = "string", format = "date"),
            ),
            Parameter(
                name = "cursor",
                `in` = ParameterIn.QUERY,
                schema = OasSchema(type = "string", maxLength = 1024),
            ),
        ],
        responses = [
            OasApiResponse(
                responseCode = "200",
                description = "Owner-scoped sanitized fill page.",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S33FillPageSuccessResponse"))],
            ),
            OasApiResponse(responseCode = "400", description = "Invalid account/date/cursor."),
            OasApiResponse(responseCode = "401", description = "Authentication is required."),
            OasApiResponse(responseCode = "404", description = "Owned account was not found."),
            OasApiResponse(responseCode = "503", description = "Stored fill source is unavailable."),
        ],
    )
    @GetMapping("/mock/accounts/{accountId}/fills")
    fun mockFills(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable accountId: String,
        request: HttpServletRequest,
    ): ApiResponse<OrderFillPageProjection> =
        fills(
            principal = principal,
            brokerageMode = BrokerageFillMode.KIS_MOCK,
            accountId = accountId,
            request = request,
        )

    @Operation(
        summary = "Owner-scoped INTERNAL_PAPER 체결 내역을 KST 날짜와 HMAC cursor로 조회한다.",
        parameters = [
            Parameter(
                name = "from",
                `in` = ParameterIn.QUERY,
                required = true,
                schema = OasSchema(type = "string", format = "date"),
            ),
            Parameter(
                name = "to",
                `in` = ParameterIn.QUERY,
                required = true,
                schema = OasSchema(type = "string", format = "date"),
            ),
            Parameter(
                name = "cursor",
                `in` = ParameterIn.QUERY,
                schema = OasSchema(type = "string", maxLength = 1024),
            ),
        ],
        responses = [
            OasApiResponse(
                responseCode = "200",
                description = "Owner-scoped sanitized fill page.",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S33FillPageSuccessResponse"))],
            ),
            OasApiResponse(responseCode = "400", description = "Invalid account/date/cursor."),
            OasApiResponse(responseCode = "401", description = "Authentication is required."),
            OasApiResponse(responseCode = "404", description = "Owned account was not found."),
            OasApiResponse(responseCode = "503", description = "Stored fill source is unavailable."),
        ],
    )
    @GetMapping("/paper/accounts/{accountId}/fills")
    fun paperFills(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable accountId: String,
        request: HttpServletRequest,
    ): ApiResponse<OrderFillPageProjection> =
        fills(
            principal = principal,
            brokerageMode = BrokerageFillMode.INTERNAL_PAPER,
            accountId = accountId,
            request = request,
        )

    private fun fills(
        principal: AppPrincipal,
        brokerageMode: BrokerageFillMode,
        accountId: String,
        request: HttpServletRequest,
    ): ApiResponse<OrderFillPageProjection> {
        val parsedAccountId = brokerageParser.parseAccountId(accountId)
        val query = fillParser.parse(request)
        val requestId = RequestIds.currentOrCreate(request)
        return ApiResponseFactory.success(
            requestId = requestId,
            data =
                queryUseCase.query(
                    actor = actor(principal, requestId),
                    brokerageMode = brokerageMode,
                    accountId = parsedAccountId,
                    fromInclusive = query.fromInclusive,
                    toExclusive = query.toExclusive,
                    cursor = query.cursor,
                ),
        )
    }

    private fun actor(
        principal: AppPrincipal,
        requestId: String,
    ): BrokerageActor =
        BrokerageActor(
            userId = principal.userId,
            role = principal.role,
            securityVersion = principal.securityVersion,
            requestId = requestId,
        )
}
