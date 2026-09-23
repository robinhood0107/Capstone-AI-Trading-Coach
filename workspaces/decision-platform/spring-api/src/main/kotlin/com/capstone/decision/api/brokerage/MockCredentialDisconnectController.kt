package com.capstone.decision.api.brokerage

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.infrastructure.brokerage.MockCredentialDisconnectService
import jakarta.servlet.http.HttpServletRequest
import org.springframework.context.annotation.Profile
import org.springframework.http.CacheControl
import org.springframework.http.MediaType
import org.springframework.http.ResponseEntity
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

@RestController
@Profile("mars-full")
@RequestMapping("/api/v1/brokerage/mock/credential", produces = [MediaType.APPLICATION_JSON_VALUE])
class MockCredentialDisconnectController(
    private val disconnect: MockCredentialDisconnectService,
) {
    @DeleteMapping
    fun remove(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ResponseEntity<MockCredentialDisconnectResponse> {
        if (request.queryString != null || !body.isNullOrBlank()) throw ApiException(ErrorCode.VALIDATION_ERROR)
        return when (disconnect.disconnect(principal.userId)) {
            "REMOVED" -> ResponseEntity.noContent().cacheControl(CacheControl.noStore()).build()
            "DISCONNECTING" ->
                ResponseEntity
                    .ok()
                    .cacheControl(CacheControl.noStore())
                    .body(MockCredentialDisconnectResponse("DISCONNECTING"))
            else -> error("BROKERAGE_CREDENTIAL_DISCONNECT_UNAVAILABLE")
        }
    }
}

data class MockCredentialDisconnectResponse(
    val state: String,
)
