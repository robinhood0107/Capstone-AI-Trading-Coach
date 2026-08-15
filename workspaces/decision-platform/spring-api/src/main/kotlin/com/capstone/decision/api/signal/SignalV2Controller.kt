package com.capstone.decision.api.signal

import com.capstone.decision.api.common.ApiError
import com.capstone.decision.api.common.ApiWarning
import com.capstone.decision.application.signal.RuntimeSignalComponent
import com.capstone.decision.application.signal.RuntimeSignalComponents
import com.capstone.decision.application.signal.RuntimeSignalComposite
import com.capstone.decision.application.signal.RuntimeSignalResponse
import com.capstone.decision.application.signal.SignalV2RuntimeService
import com.fasterxml.jackson.annotation.JsonInclude
import io.swagger.v3.oas.annotations.Operation
import io.swagger.v3.oas.annotations.media.Content
import io.swagger.v3.oas.annotations.media.Schema
import io.swagger.v3.oas.annotations.tags.Tag
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.CacheControl
import org.springframework.http.MediaType
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import java.time.Instant
import io.swagger.v3.oas.annotations.responses.ApiResponse as OasApiResponse

@JsonInclude(JsonInclude.Include.NON_NULL)
@Schema(name = "SignalV2RuntimeComponentResponse", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class SignalV2RuntimeComponentResponse(
    val status: String,
    val producer: String,
    val sourceWorkspace: String,
    val asOf: Instant? = null,
    val signal: String? = null,
    val confidence: Double? = null,
    val predictedReturn: Double? = null,
    val state: String? = null,
    val reason: String? = null,
    val modelVersion: String? = null,
    val modelReportId: String? = null,
)

@Schema(name = "SignalV2RuntimeComponentsResponse", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class SignalV2RuntimeComponentsResponse(
    val ruleBaseline: SignalV2RuntimeComponentResponse,
    val lstm: SignalV2RuntimeComponentResponse,
    val lightgbm: SignalV2RuntimeComponentResponse,
    val hmmRegime: SignalV2RuntimeComponentResponse,
)

@JsonInclude(JsonInclude.Include.NON_NULL)
@Schema(name = "SignalV2RuntimeCompositeResponse", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class SignalV2RuntimeCompositeResponse(
    val status: String,
    val signal: String? = null,
    val confidence: Double? = null,
    val predictedReturn: Double? = null,
    val reason: String? = null,
)

@JsonInclude(JsonInclude.Include.NON_NULL)
@Schema(name = "SignalV2RuntimeResponse", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class SignalV2RuntimeResponse(
    val symbol: String,
    val asOf: Instant? = null,
    val timeframe: String,
    val modelReportId: String? = null,
    val composite: SignalV2RuntimeCompositeResponse,
    val components: SignalV2RuntimeComponentsResponse,
    val warnings: List<String>,
)

@JsonInclude(JsonInclude.Include.NON_NULL)
@Schema(name = "SignalV2RuntimeSuccessResponse", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class SignalV2RuntimeSuccessResponse(
    val success: Boolean,
    val requestId: String,
    val data: SignalV2RuntimeResponse?,
    val warnings: List<ApiWarning> = emptyList(),
    val error: ApiError? = null,
)

@RestController
@RequestMapping("/api/v2/signals", produces = [MediaType.APPLICATION_JSON_VALUE])
@Tag(name = "Signal v2")
class SignalV2Controller(
    private val service: SignalV2RuntimeService,
) {
    /** symbol 외 query/user/artifact 식별자를 받지 않고 no-store runtime projection만 반환한다. */
    @Operation(
        summary = "검증된 production Signal components를 조회하며 evidence 부재는 all-ABSTAIN으로 반환한다.",
        responses = [
            OasApiResponse(
                responseCode = "200",
                content = [Content(schema = Schema(ref = "#/components/schemas/SignalV2RuntimeSuccessResponse"))],
            ),
            OasApiResponse(
                responseCode = "400",
                description = "Invalid symbol or query shape.",
                content = [Content(schema = Schema(ref = "#/components/schemas/SignalV2RuntimeErrorResponse"))],
            ),
            OasApiResponse(
                responseCode = "401",
                description = "Authentication is required.",
                content = [Content(schema = Schema(ref = "#/components/schemas/SignalV2RuntimeErrorResponse"))],
            ),
            OasApiResponse(
                responseCode = "503",
                description = "Signal evidence storage is unavailable.",
                content = [Content(schema = Schema(ref = "#/components/schemas/SignalV2RuntimeErrorResponse"))],
            ),
        ],
    )
    @GetMapping("/{symbol}")
    fun read(
        @PathVariable symbol: String,
        request: HttpServletRequest,
    ): ResponseEntity<SignalV2RuntimeResponse> {
        if (request.parameterMap.isNotEmpty()) {
            throw com.capstone.decision.api.common
                .ApiException(com.capstone.decision.api.common.ErrorCode.VALIDATION_ERROR)
        }
        val data = service.read(symbol).toDto()
        return ResponseEntity
            .ok()
            .cacheControl(CacheControl.noStore())
            .body(data)
    }
}

private fun RuntimeSignalResponse.toDto(): SignalV2RuntimeResponse =
    SignalV2RuntimeResponse(
        symbol = symbol,
        asOf = asOf,
        timeframe = timeframe,
        modelReportId = modelReportId,
        composite = composite.toDto(),
        components = components.toDto(),
        warnings = warnings,
    )

private fun RuntimeSignalComponents.toDto(): SignalV2RuntimeComponentsResponse =
    SignalV2RuntimeComponentsResponse(
        ruleBaseline = ruleBaseline.toDto(),
        lstm = lstm.toDto(),
        lightgbm = lightgbm.toDto(),
        hmmRegime = hmmRegime.toDto(),
    )

private fun RuntimeSignalComponent.toDto(): SignalV2RuntimeComponentResponse =
    SignalV2RuntimeComponentResponse(
        status,
        producer,
        sourceWorkspace,
        asOf,
        signal,
        confidence,
        predictedReturn,
        state,
        reason,
        modelVersion,
        modelReportId,
    )

private fun RuntimeSignalComposite.toDto(): SignalV2RuntimeCompositeResponse =
    SignalV2RuntimeCompositeResponse(status, signal, confidence, predictedReturn, reason)
