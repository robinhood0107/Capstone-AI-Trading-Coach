package com.capstone.decision.api.brokerage

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.infrastructure.brokerage.MockCredentialCertificationOutcome
import com.capstone.decision.infrastructure.brokerage.MockCredentialCertificationService
import io.swagger.v3.oas.annotations.Operation
import jakarta.servlet.http.HttpServletRequest
import org.springframework.context.annotation.Profile
import org.springframework.http.CacheControl
import org.springframework.http.MediaType
import org.springframework.http.ResponseEntity
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

/** User click runs one server-fixed KIS_MOCK test bound to the current owner's credential revision. */
@RestController
@Profile("mars-full")
@RequestMapping("/api/v1/brokerage/mock/credential/certify", produces = [MediaType.APPLICATION_JSON_VALUE])
class MockCredentialCertificationController(
    private val certification: MockCredentialCertificationService,
) {
    @Operation(operationId = "certifyMockBrokerCredential")
    @PostMapping
    fun certify(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ResponseEntity<ApiResponse<MockCredentialCertificationOutcome>> {
        if (request.queryString != null || !body.isNullOrBlank()) throw ApiException(ErrorCode.VALIDATION_ERROR)
        val requestId = RequestIds.currentOrCreate(request)
        return ResponseEntity
            .ok()
            .cacheControl(CacheControl.noStore())
            .body(
                ApiResponseFactory.success(
                    requestId,
                    certification.certify(principal.userId, requestId),
                ),
            )
    }

    @Operation(operationId = "acknowledgeMockBrokerCertificationRecovery")
    @PostMapping("/recovery-confirm")
    fun acknowledgeRecovery(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ResponseEntity<Void> {
        if (request.queryString != null || !body.isNullOrBlank()) throw ApiException(ErrorCode.VALIDATION_ERROR)
        certification.acknowledgeRecovery(principal.userId)
        return ResponseEntity.noContent().cacheControl(CacheControl.noStore()).build()
    }
}
