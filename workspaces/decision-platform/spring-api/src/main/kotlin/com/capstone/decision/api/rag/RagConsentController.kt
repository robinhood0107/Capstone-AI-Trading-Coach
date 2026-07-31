package com.capstone.decision.api.rag

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.rag.RagGuardHistoryService
import com.capstone.decision.application.security.AppPrincipal
import io.swagger.v3.oas.annotations.Operation
import io.swagger.v3.oas.annotations.media.Content
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.MediaType
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import io.swagger.v3.oas.annotations.media.Schema as OasSchema
import io.swagger.v3.oas.annotations.parameters.RequestBody as OasRequestBody
import io.swagger.v3.oas.annotations.responses.ApiResponse as OasApiResponse

@RestController
@RequestMapping("/api/v1/consents", produces = [MediaType.APPLICATION_JSON_VALUE])
class RagConsentController(
    private val service: RagGuardHistoryService,
    private val parser: RagRequestParser,
    private val responseBudget: RagPublicResponseBudget,
) {
    /**
     * actor/time은 JWT/server clock에서만 정하고 body는 exact RAG consent event 세 field만 허용한다.
     */
    @Operation(
        summary = "Append-only EXTERNAL_AI_RAG_V1 consent event를 기록한다.",
        requestBody =
            OasRequestBody(
                required = true,
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S44RagConsentRequest"))],
            ),
        responses = [
            OasApiResponse(
                responseCode = "200",
                content = [Content(schema = OasSchema(ref = "#/components/schemas/S44RagConsentSuccessResponse"))],
            ),
            OasApiResponse(responseCode = "400", description = "Consent type, action, or policy version is invalid."),
            OasApiResponse(responseCode = "401", description = "Authentication is required."),
            OasApiResponse(responseCode = "503", description = "Consent persistence failed closed."),
        ],
    )
    @PostMapping(consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun record(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ApiResponse<RagConsentResponse> {
        parser.requireNoQuery(request)
        val command = parser.parseConsent(body.orEmpty())
        val event =
            service.recordConsent(
                ownerUserId = principal.userId,
                action = command.action,
                policyVersion = command.policyVersion,
            )
        return responseBudget.requireWithin(
            ApiResponseFactory.success(
                requestId = RequestIds.currentOrCreate(request),
                data =
                    RagConsentResponse(
                        consentEventId = event.consentEventId,
                        consentType = event.consentType,
                        action = event.action,
                        policyVersion = event.policyVersion,
                        createdAt = event.createdAt,
                    ),
            ),
        )
    }
}
