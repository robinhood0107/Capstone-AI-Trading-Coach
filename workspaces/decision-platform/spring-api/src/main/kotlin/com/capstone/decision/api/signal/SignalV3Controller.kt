package com.capstone.decision.api.signal

import com.capstone.decision.application.signal.RuntimeSignalComponent
import com.capstone.decision.application.signal.RuntimeSignalComponents
import com.capstone.decision.application.signal.RuntimeSignalComposite
import com.capstone.decision.application.signal.RuntimeSignalResponse
import com.capstone.decision.application.signal.SignalV3RuntimeService
import com.fasterxml.jackson.annotation.JsonInclude
import io.swagger.v3.oas.annotations.Operation
import io.swagger.v3.oas.annotations.media.Content
import io.swagger.v3.oas.annotations.media.Schema
import io.swagger.v3.oas.annotations.responses.ApiResponse
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

@JsonInclude(JsonInclude.Include.NON_NULL)
@Schema(name = "SignalV3RuntimeComponentResponse", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class SignalV3RuntimeComponentResponse(
    val status: String,
    val producer: String,
    val sourceWorkspace: String,
    val asOf: Instant? = null,
    val signal: String? = null,
    val predictedReturn: Double? = null,
    val state: String? = null,
    val reason: String? = null,
    val modelVersion: String? = null,
    val modelReportId: String? = null,
)

@Schema(name = "SignalV3RuntimeComponentsResponse", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class SignalV3RuntimeComponentsResponse(
    val ruleBaseline: SignalV3RuntimeComponentResponse,
    val lstm: SignalV3RuntimeComponentResponse,
    val lightgbm: SignalV3RuntimeComponentResponse,
    val hmmRegime: SignalV3RuntimeComponentResponse,
)

@JsonInclude(JsonInclude.Include.NON_NULL)
@Schema(name = "SignalV3RuntimeCompositeResponse", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class SignalV3RuntimeCompositeResponse(
    val status: String,
    val signal: String? = null,
    val predictedReturn: Double? = null,
    val reason: String? = null,
)

@JsonInclude(JsonInclude.Include.NON_NULL)
@Schema(name = "SignalV3RuntimeResponse", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class SignalV3RuntimeResponse(
    val symbol: String,
    val asOf: Instant? = null,
    val timeframe: String,
    val modelReportId: String? = null,
    val composite: SignalV3RuntimeCompositeResponse,
    val components: SignalV3RuntimeComponentsResponse,
    val warnings: List<String>,
)

@RestController
@RequestMapping("/api/v3/signals", produces = [MediaType.APPLICATION_JSON_VALUE])
@Tag(name = "Signal v3")
class SignalV3Controller(
    private val service: SignalV3RuntimeService,
) {
    @Operation(
        operationId = "readSignalV3",
        summary = "confidence 없이 current Rule+LSTM 신호를 조회한다.",
        responses = [
            ApiResponse(
                responseCode = "200",
                content = [Content(schema = Schema(implementation = SignalV3RuntimeResponse::class))],
            ),
            ApiResponse(responseCode = "400", description = "Invalid symbol or query shape."),
            ApiResponse(responseCode = "401", description = "Authentication is required."),
            ApiResponse(responseCode = "503", description = "Signal evidence storage is unavailable."),
        ],
    )
    @GetMapping("/{symbol}")
    fun readSignalV3(
        @PathVariable symbol: String,
        request: HttpServletRequest,
    ): ResponseEntity<SignalV3RuntimeResponse> {
        if (request.parameterMap.isNotEmpty()) {
            throw com.capstone.decision.api.common
                .ApiException(com.capstone.decision.api.common.ErrorCode.VALIDATION_ERROR)
        }
        return ResponseEntity
            .ok()
            .cacheControl(CacheControl.noStore())
            .body(service.read(symbol).toV3Dto())
    }
}

private fun RuntimeSignalResponse.toV3Dto(): SignalV3RuntimeResponse =
    SignalV3RuntimeResponse(
        symbol = symbol,
        asOf = asOf,
        timeframe = timeframe,
        modelReportId = modelReportId,
        composite = composite.toV3Dto(),
        components = components.toV3Dto(),
        warnings = warnings,
    )

private fun RuntimeSignalComponents.toV3Dto(): SignalV3RuntimeComponentsResponse =
    SignalV3RuntimeComponentsResponse(
        ruleBaseline = ruleBaseline.toV3Dto(),
        lstm = lstm.toV3Dto(),
        lightgbm = lightgbm.toV3Dto(),
        hmmRegime = hmmRegime.toV3Dto(),
    )

private fun RuntimeSignalComponent.toV3Dto(): SignalV3RuntimeComponentResponse =
    SignalV3RuntimeComponentResponse(
        status = status,
        producer = producer,
        sourceWorkspace = sourceWorkspace,
        asOf = asOf,
        signal = signal,
        predictedReturn = predictedReturn,
        state = state,
        reason = reason,
        modelVersion = modelVersion,
        modelReportId = modelReportId,
    )

private fun RuntimeSignalComposite.toV3Dto(): SignalV3RuntimeCompositeResponse =
    SignalV3RuntimeCompositeResponse(
        status = status,
        signal = signal,
        predictedReturn = predictedReturn,
        reason = reason,
    )
