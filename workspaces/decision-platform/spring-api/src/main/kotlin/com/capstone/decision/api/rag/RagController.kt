package com.capstone.decision.api.rag

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.rag.RagSourceRegistryService
import com.capstone.decision.application.security.AppPrincipal
import io.swagger.v3.oas.annotations.Operation
import io.swagger.v3.oas.annotations.media.Content
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.MediaType
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import io.swagger.v3.oas.annotations.media.Schema as OasSchema
import io.swagger.v3.oas.annotations.responses.ApiResponse as OasApiResponse

@RestController
@RequestMapping("/api/v1/rag", produces = [MediaType.APPLICATION_JSON_VALUE])
class RagController(
    private val sourceRegistryService: RagSourceRegistryService,
    private val parser: RagRequestParser,
) {
    @Operation(
        summary = "인증된 subject에게 S4.1 RAG source registry metadata만 반환한다.",
        responses = [
            OasApiResponse(
                responseCode = "200",
                content = [Content(schema = OasSchema(implementation = RagSourceListResponse::class))],
            ),
            OasApiResponse(
                responseCode = "400",
                description = "Unexpected query parameter.",
                content = [Content(schema = OasSchema(implementation = S4RagErrorResponseSchema::class))],
            ),
            OasApiResponse(
                responseCode = "401",
                description = "Authentication is required.",
                content = [Content(schema = OasSchema(implementation = S4RagErrorResponseSchema::class))],
            ),
            OasApiResponse(
                responseCode = "503",
                description = "RAG source registry is unavailable.",
                content = [Content(schema = OasSchema(implementation = S4RagErrorResponseSchema::class))],
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
        return ApiResponseFactory.success(
            requestId = RequestIds.currentOrCreate(request),
            data = RagSourceListResponse(items = sources.items.map { it.toResponse() }),
        )
    }
}
