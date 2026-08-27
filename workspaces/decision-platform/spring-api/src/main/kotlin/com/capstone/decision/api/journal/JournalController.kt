package com.capstone.decision.api.journal

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.journal.JournalService
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
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PatchMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestHeader
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import io.swagger.v3.oas.annotations.parameters.RequestBody as OpenApiRequestBody
import io.swagger.v3.oas.annotations.responses.ApiResponse as OpenApiResponse

@RestController
@RequestMapping("/api/v1/journals", produces = [MediaType.APPLICATION_JSON_VALUE])
class JournalController(
    private val service: JournalService,
    private val parser: JournalRequestParser,
) {
    @Operation(operationId = "createJournal", summary = "Create owner Journal / 학습일지 생성")
    @OpenApiRequestBody(required = true, content = [Content(schema = Schema(implementation = CreateJournalRequestSchema::class))])
    @CommonJournalResponses
    @PostMapping(consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun create(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Parameter(
            name = "X-Idempotency-Key",
            `in` = ParameterIn.HEADER,
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
    ): ApiResponse<JournalResponse> {
        parser.requireNoQuery(request)
        val key = parser.requireIdempotencyKey(idempotencyKey)
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            service.create(principal.userId, key, parser.parseCreate(body.orEmpty())).toResponse(),
        )
    }

    @Operation(
        operationId = "listJournals",
        summary = "List owner Journals / 학습일지 목록",
        parameters = [
            Parameter(name = "size", `in` = ParameterIn.QUERY, schema = Schema(type = "integer", minimum = "1", maximum = "100")),
            Parameter(name = "cursor", `in` = ParameterIn.QUERY, schema = Schema(type = "string", maxLength = 512)),
        ],
    )
    @CommonJournalResponses
    @GetMapping
    fun list(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<JournalPageResponse> {
        val query = parser.parseListQuery(request)
        val page = service.list(principal.userId, query.size, query.cursor)
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            JournalPageResponse(page.items.map { it.toResponse() }, page.nextCursor),
        )
    }

    @Operation(operationId = "updateJournal", summary = "Replace owner Journal / 학습일지 전체 교체")
    @OpenApiRequestBody(required = true, content = [Content(schema = Schema(implementation = ReplaceJournalRequestSchema::class))])
    @CommonJournalResponses
    @PatchMapping("/{journalId}", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun replace(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable journalId: String,
        @Parameter(
            name = "X-Idempotency-Key",
            `in` = ParameterIn.HEADER,
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
    ): ApiResponse<JournalResponse> {
        parser.requireNoQuery(request)
        val id = parser.parseJournalId(journalId)
        val key = parser.requireIdempotencyKey(idempotencyKey)
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            service.replace(principal.userId, id, key, parser.parseReplace(body.orEmpty())).toResponse(),
        )
    }

    @Operation(operationId = "deleteJournal", summary = "Soft-delete owner Journal / 학습일지 삭제")
    @OpenApiRequestBody(required = true, content = [Content(schema = Schema(implementation = DeleteJournalRequestSchema::class))])
    @CommonJournalResponses
    @DeleteMapping("/{journalId}", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun delete(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable journalId: String,
        @Parameter(
            name = "X-Idempotency-Key",
            `in` = ParameterIn.HEADER,
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
    ): ApiResponse<JournalResponse> {
        parser.requireNoQuery(request)
        val id = parser.parseJournalId(journalId)
        val key = parser.requireIdempotencyKey(idempotencyKey)
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            service.delete(principal.userId, id, key, parser.parseDelete(body.orEmpty())).toResponse(),
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
            content = [Content(schema = Schema(implementation = P1JournalErrorResponseSchema::class))],
        ),
        OpenApiResponse(
            responseCode = "401",
            description = "Authentication required",
            content = [Content(schema = Schema(implementation = P1JournalErrorResponseSchema::class))],
        ),
        OpenApiResponse(
            responseCode = "404",
            description = "Missing, deleted, foreign owner, or foreign link",
            content = [Content(schema = Schema(implementation = P1JournalErrorResponseSchema::class))],
        ),
        OpenApiResponse(
            responseCode = "409",
            description = "Version or idempotency conflict",
            content = [Content(schema = Schema(implementation = P1JournalErrorResponseSchema::class))],
        ),
    ],
)
annotation class CommonJournalResponses
