package com.capstone.decision.api.async

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.async.StreamMetricComponent
import com.capstone.decision.application.async.StreamMetricStatus
import com.capstone.decision.application.async.StreamMetricStatusService
import com.capstone.decision.application.async.StreamMetricUnavailableException
import com.capstone.decision.application.security.AppPrincipal
import com.fasterxml.jackson.annotation.JsonProperty
import io.swagger.v3.oas.annotations.Operation
import io.swagger.v3.oas.annotations.media.Content
import io.swagger.v3.oas.annotations.media.Schema
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.MediaType
import org.springframework.security.access.prepost.PreAuthorize
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import java.math.BigDecimal
import java.time.Instant
import io.swagger.v3.oas.annotations.responses.ApiResponse as OasApiResponse

@RestController
@RequestMapping("/api/v1/stream-metrics", produces = [MediaType.APPLICATION_JSON_VALUE])
@PreAuthorize("hasRole('ADMIN')")
class StreamMetricController(
    private val service: StreamMetricStatusService,
) {
    @Operation(
        summary = "현재 DB의 ADMIN 권한을 재검증해 최신 stream metric snapshot을 조회한다.",
        responses = [
            OasApiResponse(
                responseCode = "200",
                content = [Content(schema = Schema(implementation = StreamMetricSuccessResponse::class))],
            ),
            OasApiResponse(responseCode = "401", description = "Authentication required."),
            OasApiResponse(responseCode = "403", description = "Current ADMIN role required."),
        ],
    )
    @GetMapping
    fun get(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<StreamMetricDto> {
        if (request.parameterMap.isNotEmpty()) {
            throw ApiException(ErrorCode.VALIDATION_ERROR)
        }
        val status =
            try {
                service.read(principal.userId, principal.securityVersion)
            } catch (_: StreamMetricUnavailableException) {
                throw ApiException(ErrorCode.INTERNAL_ERROR)
            } ?: throw ApiException(ErrorCode.FORBIDDEN)
        return ApiResponseFactory.success(RequestIds.currentOrCreate(request), status.toDto())
    }
}

data class StreamMetricComponentDto(
    val status: String,
    val observedAt: Instant?,
)

data class StreamMetricComponentsDto(
    val decisionDistribution: StreamMetricComponentDto,
    val signalFreshness: StreamMetricComponentDto,
    val failedJobs: StreamMetricComponentDto,
    val dlqEvents: StreamMetricComponentDto,
)

data class DecisionDistributionDto(
    @get:JsonProperty("ALLOW") val allow: Long,
    @get:JsonProperty("WARN") val warn: Long,
    @get:JsonProperty("HOLD") val hold: Long,
    @get:JsonProperty("BLOCK") val block: Long,
)

data class StreamMetricDto(
    val lastUpdatedAt: Instant?,
    val pipelineHealth: String,
    val signalStaleRatio: BigDecimal?,
    val decisionDistribution: DecisionDistributionDto,
    val failedJobCount: Long,
    val dlqEventCount: Long,
    val components: StreamMetricComponentsDto,
)

private fun StreamMetricStatus.toDto() =
    StreamMetricDto(
        lastUpdatedAt = lastUpdatedAt,
        pipelineHealth = pipelineHealth.name,
        signalStaleRatio = signalStaleRatio,
        decisionDistribution =
            DecisionDistributionDto(
                allow = decisionDistribution.allow,
                warn = decisionDistribution.warn,
                hold = decisionDistribution.hold,
                block = decisionDistribution.block,
            ),
        failedJobCount = failedJobCount,
        dlqEventCount = dlqEventCount,
        components =
            StreamMetricComponentsDto(
                decisionDistribution = decisionComponent.toDto(),
                signalFreshness = signalComponent.toDto(),
                failedJobs = failedJobComponent.toDto(),
                dlqEvents = dlqComponent.toDto(),
            ),
    )

private fun StreamMetricComponent.toDto() = StreamMetricComponentDto(status.name, observedAt)

@Schema(name = "S7StreamMetricSuccessResponse")
data class StreamMetricSuccessResponse(
    val success: Boolean,
    val requestId: String,
    val data: StreamMetricDto,
)
