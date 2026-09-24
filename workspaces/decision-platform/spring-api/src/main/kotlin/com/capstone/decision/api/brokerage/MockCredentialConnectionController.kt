package com.capstone.decision.api.brokerage

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.infrastructure.brokerage.MockCredentialConnectionService
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

/** Explicit user click starts one read-only provider check; no order is submitted. */
@RestController
@Profile("mars-full")
@RequestMapping("/api/v1/brokerage/mock/credential/connect", produces = [MediaType.APPLICATION_JSON_VALUE])
class MockCredentialConnectionController(
    private val connection: MockCredentialConnectionService,
) {
    @PostMapping
    fun verify(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ResponseEntity<Void> {
        if (request.queryString != null || !body.isNullOrBlank()) throw ApiException(ErrorCode.VALIDATION_ERROR)
        connection.verify(principal.userId, RequestIds.currentOrCreate(request))
        return ResponseEntity.noContent().cacheControl(CacheControl.noStore()).build()
    }
}
