package com.capstone.decision.api.strongllm

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.infrastructure.vertex.OperatorAiBudgetPolicy
import com.capstone.decision.infrastructure.vertex.OperatorAiBudgetPolicyService
import jakarta.servlet.http.HttpServletRequest
import org.springframework.context.annotation.Profile
import org.springframework.http.CacheControl
import org.springframework.http.MediaType
import org.springframework.http.ResponseEntity
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import tools.jackson.core.JacksonException
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.json.JsonMapper

@RestController
@Profile("mars-full")
@RequestMapping("/api/v1/admin/ai-budget", produces = [MediaType.APPLICATION_JSON_VALUE])
class OperatorAiBudgetController(
    private val service: OperatorAiBudgetPolicyService,
) {
    private val parser = OperatorAiBudgetRequestParser()

    @GetMapping
    fun read(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ResponseEntity<ApiResponse<OperatorAiBudgetPolicy>> {
        requireNoQuery(request)
        return ResponseEntity
            .ok()
            .cacheControl(CacheControl.noStore())
            .body(ApiResponseFactory.success(RequestIds.currentOrCreate(request), service.read(principal)))
    }

    @PutMapping(consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun update(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ResponseEntity<ApiResponse<OperatorAiBudgetPolicy>> {
        requireNoQuery(request)
        val input = parser.parse(body.orEmpty())
        return ResponseEntity
            .ok()
            .cacheControl(CacheControl.noStore())
            .body(
                ApiResponseFactory.success(
                    RequestIds.currentOrCreate(request),
                    service.update(principal, input.first, input.second),
                ),
            )
    }

    private fun requireNoQuery(request: HttpServletRequest) {
        if (request.queryString != null) throw ApiException(ErrorCode.VALIDATION_ERROR)
    }
}

/** Exact integer-cents body avoids floating-point rounding and duplicate-key ambiguity. */
internal class OperatorAiBudgetRequestParser {
    private val mapper =
        JsonMapper
            .builder(
                JsonFactory
                    .builder()
                    .streamReadConstraints(
                        StreamReadConstraints
                            .builder()
                            .maxDocumentLength(128)
                            .maxNestingDepth(2)
                            .maxTokenCount(8)
                            .build(),
                    ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                    .build(),
            ).build()

    fun parse(body: String): Pair<Long, Long> {
        val root =
            try {
                mapper.readTree(body)
            } catch (_: JacksonException) {
                throw ApiException(ErrorCode.VALIDATION_ERROR)
            }
        if (root == null || !root.isObject || root.properties().map { it.key }.toSet() != FIELDS) {
            throw ApiException(ErrorCode.VALIDATION_ERROR)
        }
        val cap = root.get("dailySoftCapCents")?.takeIf { it.isIntegralNumber }?.longValue()
        val revision = root.get("expectedRevision")?.takeIf { it.isIntegralNumber }?.longValue()
        if (cap == null || cap < 0 || revision == null || revision < 1) {
            throw ApiException(ErrorCode.VALIDATION_ERROR)
        }
        return cap to revision
    }

    private companion object {
        val FIELDS = setOf("dailySoftCapCents", "expectedRevision")
    }
}
