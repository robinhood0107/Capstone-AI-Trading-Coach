package com.capstone.decision.api.dashboard

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.dashboard.ArtifactIngestStatusView
import com.capstone.decision.application.dashboard.DashboardArtifactKind
import com.capstone.decision.application.dashboard.DashboardUnavailableException
import com.capstone.decision.application.dashboard.DashboardViewService
import com.capstone.decision.application.security.AppPrincipal
import io.swagger.v3.oas.annotations.Operation
import io.swagger.v3.oas.annotations.media.Content
import io.swagger.v3.oas.annotations.media.Schema
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.MediaType
import org.springframework.security.access.prepost.PreAuthorize
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import tools.jackson.databind.JsonNode
import java.time.Instant
import io.swagger.v3.oas.annotations.responses.ApiResponse as OasApiResponse

@RestController
@RequestMapping("/api/v1/dashboard", produces = [MediaType.APPLICATION_JSON_VALUE])
@PreAuthorize("hasAnyRole('USER','ADMIN')")
class DashboardController(
    private val service: DashboardViewService,
) {
    @Operation(summary = "Owner-scoped sanitized model evaluation ViewModel.")
    @GetMapping("/model-evaluations/{runId}")
    fun modelEvaluation(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable runId: String,
        request: HttpServletRequest,
    ): ApiResponse<JsonNode> = artifact(principal, runId, DashboardArtifactKind.MODEL_EVALUATION, request)

    @Operation(summary = "Owner-scoped sanitized backtest ViewModel.")
    @GetMapping("/backtests/{runId}")
    fun backtest(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable runId: String,
        request: HttpServletRequest,
    ): ApiResponse<JsonNode> = artifact(principal, runId, DashboardArtifactKind.BACKTEST, request)

    @Operation(summary = "Owner-scoped persisted Decision/Risk ViewModel.")
    @GetMapping("/risk-results/{decisionId}")
    fun risk(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable decisionId: String,
        request: HttpServletRequest,
    ): ApiResponse<JsonNode> {
        exactRequest(request)
        if (!DECISION_ID.matches(decisionId)) invalid("/path/decisionId")
        return success(request, protect { service.risk(principal.userId, principal.securityVersion, decisionId) })
    }

    @Operation(summary = "Owner-scoped bounded RAG source ViewModel without raw content or locator.")
    @GetMapping("/rag-sources/{answerId}")
    fun rag(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable answerId: String,
        request: HttpServletRequest,
    ): ApiResponse<JsonNode> {
        exactRequest(request)
        if (!ANSWER_ID.matches(answerId)) invalid("/path/answerId")
        return success(request, protect { service.rag(principal.userId, principal.securityVersion, answerId) })
    }

    private fun artifact(
        principal: AppPrincipal,
        runId: String,
        kind: DashboardArtifactKind,
        request: HttpServletRequest,
    ): ApiResponse<JsonNode> {
        exactRequest(request)
        if (!RUN_ID.matches(runId)) invalid("/path/runId")
        return success(request, protect { service.artifact(principal.userId, principal.securityVersion, kind, runId) })
    }

    private fun success(
        request: HttpServletRequest,
        data: JsonNode?,
    ): ApiResponse<JsonNode> =
        ApiResponseFactory.success(RequestIds.currentOrCreate(request), data ?: throw ApiException(ErrorCode.NOT_FOUND))

    private fun exactRequest(request: HttpServletRequest) {
        if (request.parameterMap.isNotEmpty()) invalid("/query")
    }

    private fun invalid(field: String): Nothing =
        throw ApiException(
            ErrorCode.VALIDATION_ERROR,
            details = mapOf("violations" to listOf(mapOf("field" to field, "reason" to "INVALID_FORMAT"))),
        )

    private fun <T> protect(block: () -> T): T =
        try {
            block()
        } catch (_: DashboardUnavailableException) {
            throw ApiException(ErrorCode.INTERNAL_ERROR)
        }

    private companion object {
        val RUN_ID = Regex("^(run|demo)_[A-Za-z0-9_-]{8,96}$")
        val DECISION_ID = Regex("^dec_[A-Za-z0-9_-]{8,96}$")
        val ANSWER_ID = Regex("^rag_[A-Za-z0-9_-]{12,96}$")
    }
}

@RestController
@RequestMapping("/api/v1/artifacts/ingest-status", produces = [MediaType.APPLICATION_JSON_VALUE])
@PreAuthorize("hasRole('ADMIN')")
class ArtifactIngestStatusController(
    private val service: DashboardViewService,
) {
    @Operation(
        summary = "Current DB ADMIN-only bounded artifact ingest status.",
        responses = [
            OasApiResponse(
                responseCode = "200",
                content = [Content(schema = Schema(implementation = ArtifactIngestStatusSuccessResponse::class))],
            ),
        ],
    )
    @GetMapping
    fun list(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<ArtifactIngestStatusListDto> {
        if (request.parameterMap.isNotEmpty()) throw ApiException(ErrorCode.VALIDATION_ERROR)
        val items =
            try {
                service.artifactStatuses(principal.userId, principal.securityVersion)
            } catch (_: DashboardUnavailableException) {
                throw ApiException(ErrorCode.INTERNAL_ERROR)
            } ?: throw ApiException(ErrorCode.FORBIDDEN)
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            ArtifactIngestStatusListDto(items.map(ArtifactIngestStatusView::toDto)),
        )
    }
}

data class ArtifactIngestStatusDto(
    val artifactId: String,
    val fileName: String,
    val producer: String,
    val runId: String,
    val fileHash: String,
    val schemaVersion: String,
    val status: String,
    val lastIngestedAt: Instant?,
    val duplicate: Boolean,
)

data class ArtifactIngestStatusListDto(
    val items: List<ArtifactIngestStatusDto>,
)

private fun ArtifactIngestStatusView.toDto() =
    ArtifactIngestStatusDto(
        artifactId,
        fileName,
        producer,
        runId,
        fileHash,
        schemaVersion,
        status,
        lastIngestedAt,
        duplicate,
    )

@Schema(name = "S8ArtifactIngestStatusSuccessResponse")
data class ArtifactIngestStatusSuccessResponse(
    val success: Boolean,
    val requestId: String,
    val data: ArtifactIngestStatusListDto,
)
