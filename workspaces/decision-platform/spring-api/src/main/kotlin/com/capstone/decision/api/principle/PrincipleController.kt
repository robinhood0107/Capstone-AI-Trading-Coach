package com.capstone.decision.api.principle

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.principle.PrincipleActor
import com.capstone.decision.application.principle.PrincipleService
import com.capstone.decision.infrastructure.principle.PrincipleCatalog
import com.capstone.decision.infrastructure.security.AppPrincipal
import io.swagger.v3.oas.annotations.Operation
import io.swagger.v3.oas.annotations.Parameter
import io.swagger.v3.oas.annotations.enums.ParameterIn
import io.swagger.v3.oas.annotations.media.Content
import io.swagger.v3.oas.annotations.media.Schema
import io.swagger.v3.oas.annotations.responses.ApiResponses
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.HttpStatus
import org.springframework.http.MediaType
import org.springframework.http.ResponseEntity
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import java.net.URI
import io.swagger.v3.oas.annotations.parameters.RequestBody as OpenApiRequestBody
import io.swagger.v3.oas.annotations.responses.ApiResponse as OpenApiResponse

// actor는 인증된 AppPrincipal에서만 받고 owner ID를 request body/query에 노출하지 않는다.
@RestController
@RequestMapping("/api/v1", produces = [MediaType.APPLICATION_JSON_VALUE])
class PrincipleController(
    private val service: PrincipleService,
    private val parser: PrincipleRequestParser,
    private val catalog: PrincipleCatalog,
) {
    @Operation(
        operationId = "listPrinciplePresets",
        summary = "원칙 프리셋 조회 / List Principle presets",
    )
    @ApiResponses(
        value = [
            OpenApiResponse(responseCode = "200", description = "Preset catalog"),
            OpenApiResponse(responseCode = "401", description = "Unauthorized"),
            OpenApiResponse(responseCode = "403", description = "Forbidden"),
        ],
    )
    @GetMapping("/principle-presets")
    fun listPresets(request: HttpServletRequest): ApiResponse<PrinciplePresetListData> {
        parser.requireNoQuery(request)
        return ApiResponseFactory.success(
            requestId = RequestIds.currentOrCreate(request),
            data =
                PrinciplePresetListData(
                    disclaimer =
                        PrincipleDisclaimerResponse(
                            ko = catalog.disclaimerKo,
                            en = catalog.disclaimerEn,
                        ),
                    items = service.listPresets().map { it.toResponse() },
                ),
        )
    }

    @Operation(
        operationId = "createPrinciple",
        summary = "사용자 원칙 생성 / Create an owned Principle",
    )
    @OpenApiRequestBody(
        required = true,
        content = [Content(schema = Schema(implementation = PrincipleCreateRequestSchema::class))],
    )
    @ApiResponses(
        value = [
            OpenApiResponse(responseCode = "201", description = "Created"),
            OpenApiResponse(responseCode = "400", description = "Validation error"),
            OpenApiResponse(responseCode = "401", description = "Unauthorized"),
            OpenApiResponse(responseCode = "403", description = "Forbidden"),
            OpenApiResponse(responseCode = "413", description = "Payload too large"),
        ],
    )
    @PostMapping("/principles", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun create(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ResponseEntity<ApiResponse<PrincipleCurrentResponse>> {
        parser.requireNoQuery(request)
        val current =
            service.create(
                actor = principal.toActor(request),
                command = parser.parseCreate(body.orEmpty()),
            )
        return ResponseEntity
            .status(HttpStatus.CREATED)
            .location(URI.create("/api/v1/principles/${current.principleId.value}"))
            .body(
                ApiResponseFactory.success(
                    requestId = RequestIds.currentOrCreate(request),
                    data = current.toResponse(),
                ),
            )
    }

    @Operation(
        operationId = "listPrinciples",
        summary = "사용자 원칙 목록 / List owned Principles",
        parameters = [
            Parameter(name = "cursor", `in` = ParameterIn.QUERY, schema = Schema(type = "string", maxLength = 2048)),
            Parameter(
                name = "size",
                `in` = ParameterIn.QUERY,
                schema = Schema(type = "integer", format = "int32", minimum = "1", maximum = "200"),
            ),
            Parameter(
                name = "sort",
                `in` = ParameterIn.QUERY,
                schema = Schema(type = "string", allowableValues = ["UPDATED_AT_DESC", "UPDATED_AT_ASC"]),
            ),
        ],
    )
    @ApiResponses(
        value = [
            OpenApiResponse(responseCode = "200", description = "Owned Principle page"),
            OpenApiResponse(responseCode = "400", description = "Validation error"),
            OpenApiResponse(responseCode = "401", description = "Unauthorized"),
            OpenApiResponse(responseCode = "403", description = "Forbidden"),
        ],
    )
    @GetMapping("/principles")
    fun list(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<PrincipleOwnerListData> {
        val page = service.list(principal.userId, parser.parseOwnerQuery(request))
        return ApiResponseFactory.success(
            requestId = RequestIds.currentOrCreate(request),
            data =
                PrincipleOwnerListData(
                    items = page.items.map { it.toResponse() },
                    nextCursor = page.nextCursor,
                ),
        )
    }

    @Operation(
        operationId = "getPrinciple",
        summary = "사용자 원칙 상세 / Get an owned Principle",
    )
    @ApiResponses(
        value = [
            OpenApiResponse(responseCode = "200", description = "Current Principle"),
            OpenApiResponse(responseCode = "400", description = "Validation error"),
            OpenApiResponse(responseCode = "401", description = "Unauthorized"),
            OpenApiResponse(responseCode = "403", description = "Forbidden"),
            OpenApiResponse(responseCode = "404", description = "Missing or cross-owner"),
        ],
    )
    @GetMapping("/principles/{principleId}")
    fun get(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable principleId: String,
        request: HttpServletRequest,
    ): ApiResponse<PrincipleCurrentResponse> {
        parser.requireNoQuery(request)
        val current = service.get(principal.userId, parser.parsePrincipleId(principleId))
        return ApiResponseFactory.success(
            requestId = RequestIds.currentOrCreate(request),
            data = current.toResponse(),
        )
    }

    @Operation(
        operationId = "updatePrinciple",
        summary = "사용자 원칙 전체 교체 / Replace an owned Principle",
    )
    @OpenApiRequestBody(
        required = true,
        content = [Content(schema = Schema(implementation = PrincipleUpdateRequestSchema::class))],
    )
    @ApiResponses(
        value = [
            OpenApiResponse(responseCode = "200", description = "Current Principle"),
            OpenApiResponse(responseCode = "400", description = "Validation error"),
            OpenApiResponse(responseCode = "401", description = "Unauthorized"),
            OpenApiResponse(responseCode = "403", description = "Forbidden"),
            OpenApiResponse(responseCode = "404", description = "Missing or cross-owner"),
            OpenApiResponse(responseCode = "409", description = "Version conflict or exhausted"),
            OpenApiResponse(responseCode = "413", description = "Payload too large"),
        ],
    )
    @PutMapping("/principles/{principleId}", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun update(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable principleId: String,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ApiResponse<PrincipleCurrentResponse> {
        parser.requireNoQuery(request)
        val current =
            service.update(
                actor = principal.toActor(request),
                principleId = parser.parsePrincipleId(principleId),
                command = parser.parseUpdate(body.orEmpty()),
            )
        return ApiResponseFactory.success(
            requestId = RequestIds.currentOrCreate(request),
            data = current.toResponse(),
        )
    }

    @Operation(
        operationId = "listPrincipleVersions",
        summary = "원칙 버전 이력 / List immutable Principle versions",
        parameters = [
            Parameter(name = "cursor", `in` = ParameterIn.QUERY, schema = Schema(type = "string", maxLength = 2048)),
            Parameter(
                name = "size",
                `in` = ParameterIn.QUERY,
                schema = Schema(type = "integer", format = "int32", minimum = "1", maximum = "200"),
            ),
            Parameter(
                name = "sort",
                `in` = ParameterIn.QUERY,
                schema = Schema(type = "string", allowableValues = ["VERSION_DESC", "VERSION_ASC"]),
            ),
        ],
    )
    @ApiResponses(
        value = [
            OpenApiResponse(responseCode = "200", description = "Immutable version page"),
            OpenApiResponse(responseCode = "400", description = "Validation error"),
            OpenApiResponse(responseCode = "401", description = "Unauthorized"),
            OpenApiResponse(responseCode = "403", description = "Forbidden"),
            OpenApiResponse(responseCode = "404", description = "Missing or cross-owner"),
        ],
    )
    @GetMapping("/principles/{principleId}/versions")
    fun listVersions(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable principleId: String,
        request: HttpServletRequest,
    ): ApiResponse<PrincipleHistoryData> {
        val page =
            service.listVersions(
                userId = principal.userId,
                principleId = parser.parsePrincipleId(principleId),
                query = parser.parseHistoryQuery(request),
            )
        return ApiResponseFactory.success(
            requestId = RequestIds.currentOrCreate(request),
            data =
                PrincipleHistoryData(
                    items = page.items.map { it.toResponse() },
                    nextCursor = page.nextCursor,
                ),
        )
    }

    private fun AppPrincipal.toActor(request: HttpServletRequest): PrincipleActor =
        PrincipleActor(
            userId = userId,
            role = role.name,
            requestId = RequestIds.currentOrCreate(request),
        )
}
