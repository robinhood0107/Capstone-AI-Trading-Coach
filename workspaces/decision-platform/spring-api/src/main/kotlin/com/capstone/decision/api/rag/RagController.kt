package com.capstone.decision.api.rag

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.rag.RagAnswerProjection
import com.capstone.decision.application.rag.RagGuardHistoryService
import com.capstone.decision.application.rag.RagHistoryDetail
import com.capstone.decision.application.rag.RagHistoryPage
import com.capstone.decision.application.rag.RagSourceRegistryService
import com.capstone.decision.application.security.AppPrincipal
import io.swagger.v3.oas.annotations.Operation
import io.swagger.v3.oas.annotations.Parameter
import io.swagger.v3.oas.annotations.media.Content
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.MediaType
import org.springframework.http.ResponseEntity
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.DeleteMapping
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
@RequestMapping("/api/v1/rag", produces = [MediaType.APPLICATION_JSON_VALUE])
class RagController(
    private val sourceRegistryService: RagSourceRegistryService,
    private val guardHistoryService: RagGuardHistoryService,
    private val parser: RagRequestParser,
    private val responseBudget: RagPublicResponseBudget,
) {
    @Operation(
        summary = "Fixture-only RAG guard와 암호화 history 경계를 실행한다.",
        requestBody =
            OasRequestBody(
                required = true,
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S44RagAskRequest"))],
            ),
        responses = [
            OasApiResponse(
                responseCode = "200",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S44RagAnswerSuccessResponse"))],
            ),
            OasApiResponse(responseCode = "400", description = "Strict request validation failed."),
            OasApiResponse(responseCode = "401", description = "Authentication is required."),
            OasApiResponse(responseCode = "409", description = "Idempotency conflict, in-progress, or result unavailable."),
            OasApiResponse(responseCode = "429", description = "Owner RAG rate limit exceeded."),
            OasApiResponse(responseCode = "503", description = "Guard, crypto, or persistence failed closed."),
        ],
    )
    @PostMapping("/ask", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun ask(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Parameter(
            required = true,
            schema = OasSchema(pattern = "^[A-Za-z0-9._~-]{16,128}$"),
            description = "Raw value is HMACed at the request boundary and never persisted.",
        )
        @RequestHeader(name = "X-Idempotency-Key", required = false) idempotencyKey: String?,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ApiResponse<RagAnswerProjection> {
        parser.requireNoQuery(request)
        val rawKey = parser.requireIdempotencyKey(idempotencyKey)
        val command = parser.parseAsk(body.orEmpty())
        val requestId = RequestIds.currentOrCreate(request)
        return responseBudget.requireWithin(
            ApiResponseFactory.success(
                requestId = requestId,
                data =
                    guardHistoryService.ask(
                        ownerUserId = principal.userId,
                        requestId = requestId,
                        rawIdempotencyKey = rawKey,
                        command = command,
                    ),
            ),
        )
    }

    @Operation(
        summary = "인증된 subject에게 S4.1 RAG source registry metadata만 반환한다.",
        responses = [
            OasApiResponse(
                responseCode = "200",
                content = [
                    Content(
                        schema = OasSchema(ref = "#/components/schemas/S4RagSourceListSuccessResponse"),
                    ),
                ],
            ),
            OasApiResponse(
                responseCode = "400",
                description = "Unexpected query parameter.",
                content = [
                    Content(
                        schema = OasSchema(ref = "#/components/schemas/S4RagValidationErrorResponse"),
                    ),
                ],
            ),
            OasApiResponse(
                responseCode = "401",
                description = "Authentication is required.",
                content = [
                    Content(
                        schema = OasSchema(ref = "#/components/schemas/S4RagUnauthorizedErrorResponse"),
                    ),
                ],
            ),
            OasApiResponse(
                responseCode = "503",
                description = "RAG source registry is unavailable.",
                content = [
                    Content(
                        schema = OasSchema(ref = "#/components/schemas/S4RagUnavailableErrorResponse"),
                    ),
                ],
            ),
        ],
    )
    @GetMapping("/sources")
    fun listSources(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<RagSourceListResponse> {
        parser.requireNoQuery(request)
        val sources = sourceRegistryService.listSources(principal.userId)
        return responseBudget.requireWithin(
            ApiResponseFactory.success(
                requestId = RequestIds.currentOrCreate(request),
                data = RagSourceListResponse(items = sources.items.map { it.toResponse() }),
            ),
        )
    }

    @Operation(
        summary = "질문·답변 복호화 없이 owner의 RAG history metadata만 page 조회한다.",
        parameters = [
            Parameter(
                name = "cursor",
                description = "Owner-bound opaque HMAC cursor.",
                schema = OasSchema(maxLength = 512),
            ),
            Parameter(
                name = "limit",
                schema = OasSchema(type = "integer", minimum = "1", maximum = "50", defaultValue = "20"),
            ),
        ],
        responses = [
            OasApiResponse(
                responseCode = "200",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S44RagHistoryPageSuccessResponse"))],
            ),
            OasApiResponse(responseCode = "400", description = "Invalid cursor, limit, or unknown query."),
            OasApiResponse(responseCode = "401", description = "Authentication is required."),
        ],
    )
    @GetMapping("/history")
    fun listHistory(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<RagHistoryPage> {
        val query = parser.parseHistoryQuery(request)
        return responseBudget.requireWithin(
            ApiResponseFactory.success(
                requestId = RequestIds.currentOrCreate(request),
                data =
                    guardHistoryService.listHistory(
                        ownerUserId = principal.userId,
                        cursor = query.cursor,
                        limit = query.limit,
                    ),
            ),
        )
    }

    @Operation(
        summary = "Owner-scoped RAG history 한 건만 복호화하고 citation access를 재검증한다.",
        responses = [
            OasApiResponse(
                responseCode = "200",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S44RagHistoryDetailSuccessResponse"))],
            ),
            OasApiResponse(responseCode = "401", description = "Authentication is required."),
            OasApiResponse(responseCode = "404", description = "Owned unexpired history was not found."),
            OasApiResponse(responseCode = "503", description = "Ciphertext or citation state failed closed."),
        ],
    )
    @GetMapping("/history/{answerId}")
    fun getHistory(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Parameter(schema = OasSchema(pattern = "^rag_ans_[0-9a-f]{32}$"))
        @PathVariable answerId: String,
        request: HttpServletRequest,
    ): ApiResponse<RagHistoryDetail> {
        parser.requireNoQuery(request)
        return responseBudget.requireWithin(
            ApiResponseFactory.success(
                requestId = RequestIds.currentOrCreate(request),
                data =
                    guardHistoryService.getHistory(
                        principal.userId,
                        parser.parseAnswerId(answerId),
                    ),
            ),
        )
    }

    @Operation(
        summary = "Owner predicate 단일 delete를 수행하고 존재 여부와 무관하게 204를 반환한다.",
        responses = [
            OasApiResponse(responseCode = "204", description = "Deletion outcome is intentionally indistinguishable."),
            OasApiResponse(responseCode = "401", description = "Authentication is required."),
        ],
    )
    @DeleteMapping("/history/{answerId}")
    fun deleteHistory(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Parameter(schema = OasSchema(pattern = "^rag_ans_[0-9a-f]{32}$"))
        @PathVariable answerId: String,
        request: HttpServletRequest,
    ): ResponseEntity<Void> {
        parser.requireNoQuery(request)
        guardHistoryService.deleteHistory(
            principal.userId,
            parser.parseAnswerId(answerId),
        )
        return ResponseEntity.noContent().build()
    }

    @Operation(
        summary = "Owner answer에 boolean helpful 하나만 멱등 저장한다.",
        requestBody =
            OasRequestBody(
                required = true,
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S44RagFeedbackRequest"))],
            ),
        responses = [
            OasApiResponse(
                responseCode = "200",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S44RagFeedbackSuccessResponse"))],
            ),
            OasApiResponse(responseCode = "400", description = "Only boolean helpful is accepted."),
            OasApiResponse(responseCode = "401", description = "Authentication is required."),
            OasApiResponse(responseCode = "404", description = "Owned unexpired answer was not found."),
        ],
    )
    @PostMapping(
        "/answers/{answerId}/feedback",
        consumes = [MediaType.APPLICATION_JSON_VALUE],
    )
    fun feedback(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Parameter(schema = OasSchema(pattern = "^rag_ans_[0-9a-f]{32}$"))
        @PathVariable answerId: String,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ApiResponse<RagFeedbackResponse> {
        parser.requireNoQuery(request)
        val parsedAnswerId = parser.parseAnswerId(answerId)
        val helpful = parser.parseFeedback(body.orEmpty())
        guardHistoryService.feedback(principal.userId, parsedAnswerId, helpful)
        return responseBudget.requireWithin(
            ApiResponseFactory.success(
                requestId = RequestIds.currentOrCreate(request),
                data = RagFeedbackResponse(parsedAnswerId, helpful),
            ),
        )
    }
}
