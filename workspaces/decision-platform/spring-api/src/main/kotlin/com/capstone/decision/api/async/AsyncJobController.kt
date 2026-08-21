package com.capstone.decision.api.async

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.async.AsyncJobError
import com.capstone.decision.application.async.AsyncJobPageQuery
import com.capstone.decision.application.async.AsyncJobStatus
import com.capstone.decision.application.async.AsyncJobStatusService
import com.capstone.decision.application.async.AsyncJobStatusUnavailableException
import com.capstone.decision.application.async.AsyncJobType
import com.capstone.decision.application.async.AsyncJobView
import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.infrastructure.async.AsyncJobCursorCodec
import com.capstone.decision.infrastructure.async.InvalidAsyncJobCursorException
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
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController
import java.time.Instant
import io.swagger.v3.oas.annotations.responses.ApiResponse as OasApiResponse

@RestController
@RequestMapping("/api/v1/async-jobs", produces = [MediaType.APPLICATION_JSON_VALUE])
@PreAuthorize("hasRole('ADMIN')")
class AsyncJobController(
    private val service: AsyncJobStatusService,
    private val cursorCodec: AsyncJobCursorCodec,
) {
    @Operation(
        summary = "현재 DB의 ADMIN 권한을 재검증해 bounded async job 상태를 조회한다.",
        responses = [
            OasApiResponse(
                responseCode = "200",
                content = [Content(schema = Schema(implementation = AsyncJobStatusSuccessResponse::class))],
            ),
            OasApiResponse(responseCode = "401", description = "Authentication required."),
            OasApiResponse(responseCode = "403", description = "Current ADMIN role required."),
            OasApiResponse(responseCode = "404", description = "Job not found."),
        ],
    )
    @GetMapping("/{jobId}")
    fun get(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable jobId: String,
        request: HttpServletRequest,
    ): ApiResponse<AsyncJobDto> {
        requireJobId(jobId)
        requireNoQuery(request)
        val item =
            protect {
                service.get(principal.userId, principal.securityVersion, jobId)
            } ?: throw ApiException(ErrorCode.NOT_FOUND)
        return ApiResponseFactory.success(RequestIds.currentOrCreate(request), item.toDto())
    }

    @Operation(
        summary = "현재 DB의 ADMIN 권한을 재검증해 bounded async job 목록을 조회한다.",
        responses = [
            OasApiResponse(
                responseCode = "200",
                content = [Content(schema = Schema(implementation = AsyncJobListSuccessResponse::class))],
            ),
            OasApiResponse(responseCode = "400", description = "Invalid filter or cursor."),
            OasApiResponse(responseCode = "401", description = "Authentication required."),
            OasApiResponse(responseCode = "403", description = "Current ADMIN role required."),
        ],
    )
    @GetMapping
    fun list(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestParam(required = false) status: String?,
        @RequestParam(required = false, name = "type") typeValue: String?,
        @RequestParam(required = false) cursor: String?,
        @RequestParam(required = false, defaultValue = "50") size: String,
        request: HttpServletRequest,
    ): ApiResponse<AsyncJobListDto> {
        requireExactListQuery(request)
        val parsedStatus = parseEnum<AsyncJobStatus>(status, "/query/status")
        val parsedType = parseEnum<AsyncJobType>(typeValue, "/query/type")
        val parsedSize = size.toIntOrNull()?.takeIf { it in 1..100 } ?: invalid("/query/size")
        val decoded =
            cursor?.let {
                try {
                    cursorCodec.decode(it, principal.userId, parsedStatus, parsedType, parsedSize)
                } catch (_: InvalidAsyncJobCursorException) {
                    invalid("/query/cursor")
                }
            }
        val rows =
            protect {
                service.list(
                    AsyncJobPageQuery(
                        actorUserId = principal.userId,
                        securityVersion = principal.securityVersion,
                        status = parsedStatus,
                        type = parsedType,
                        beforeRequestedAt = decoded?.beforeRequestedAt,
                        beforeJobId = decoded?.beforeJobId,
                        size = parsedSize,
                    ),
                )
            }
        val items = rows.take(parsedSize)
        val nextCursor =
            if (rows.size > parsedSize) {
                val last = items.last()
                cursorCodec.encode(
                    principal.userId,
                    parsedStatus,
                    parsedType,
                    parsedSize,
                    last.requestedAt,
                    last.jobId,
                )
            } else {
                null
            }
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            AsyncJobListDto(items.map(AsyncJobView::toDto), nextCursor),
        )
    }

    private fun requireNoQuery(request: HttpServletRequest) {
        if (request.parameterMap.isNotEmpty()) invalid("/query")
    }

    private fun requireExactListQuery(request: HttpServletRequest) {
        val unknown = request.parameterMap.keys.firstOrNull { it !in LIST_QUERY_FIELDS }
        if (unknown != null) invalid("/query")
        if (request.parameterMap.values.any { it.size != 1 }) invalid("/query")
    }

    private fun requireJobId(jobId: String) {
        if (!JOB_ID.matches(jobId)) invalid("/path/jobId")
    }

    private inline fun <reified T : Enum<T>> parseEnum(
        value: String?,
        field: String,
    ): T? = value?.let { runCatching { enumValueOf<T>(it) }.getOrElse { invalid(field) } }

    private fun invalid(field: String): Nothing =
        throw ApiException(
            ErrorCode.VALIDATION_ERROR,
            details = mapOf("violations" to listOf(mapOf("field" to field, "reason" to "INVALID_FORMAT"))),
        )

    private fun <T> protect(block: () -> T): T =
        try {
            block()
        } catch (_: AsyncJobStatusUnavailableException) {
            throw ApiException(ErrorCode.INTERNAL_ERROR)
        }

    private companion object {
        val JOB_ID = Regex("^job_[A-Za-z0-9_-]{8,96}$")
        val LIST_QUERY_FIELDS = setOf("status", "type", "cursor", "size")
    }
}

data class AsyncJobErrorDto(
    val code: String,
    val `class`: String,
)

data class AsyncJobDto(
    val jobId: String,
    val type: String,
    val status: String,
    val requestedAt: Instant,
    val startedAt: Instant?,
    val completedAt: Instant?,
    val sourceId: String?,
    val artifactId: String?,
    val resultRef: String?,
    val error: AsyncJobErrorDto?,
)

data class AsyncJobListDto(
    val items: List<AsyncJobDto>,
    val nextCursor: String?,
)

private fun AsyncJobView.toDto(): AsyncJobDto =
    AsyncJobDto(
        jobId = jobId,
        type = type.name,
        status = status.name,
        requestedAt = requestedAt,
        startedAt = startedAt,
        completedAt = completedAt,
        sourceId = sourceId,
        artifactId = artifactId,
        resultRef = resultRef,
        error = error?.toDto(),
    )

private fun AsyncJobError.toDto() = AsyncJobErrorDto(code, errorClass)

@Schema(name = "S7AsyncJobStatusSuccessResponse")
data class AsyncJobStatusSuccessResponse(
    val success: Boolean,
    val requestId: String,
    val data: AsyncJobDto,
)

@Schema(name = "S7AsyncJobListSuccessResponse")
data class AsyncJobListSuccessResponse(
    val success: Boolean,
    val requestId: String,
    val data: AsyncJobListDto,
)
