package com.capstone.decision.api.auth

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.infrastructure.security.GoogleOidcHandoff
import com.capstone.decision.infrastructure.security.GoogleOidcProperties
import io.swagger.v3.oas.annotations.Operation
import io.swagger.v3.oas.annotations.security.SecurityRequirements
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.springframework.context.annotation.Profile
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

/** Same-origin, one-use handoff after Spring Security has consumed Google's code, state, and nonce. */
@RestController
@Profile("mars-full")
@RequestMapping("/api/v1/auth/oidc")
class GoogleOidcExchangeController(
    private val handoff: GoogleOidcHandoff,
    properties: GoogleOidcProperties,
) {
    private val publicOrigin = properties.validatedOrigin()

    @Operation(operationId = "exchangeGoogleOidcSession")
    @SecurityRequirements
    @PostMapping("/exchange")
    fun exchange(
        request: HttpServletRequest,
        response: HttpServletResponse,
    ): ApiResponse<LoginResponse> {
        if (
            request.getHeader("Origin") != publicOrigin ||
            request.queryString != null ||
            request.contentLengthLong > 0
        ) {
            throw ApiException(ErrorCode.FORBIDDEN)
        }
        val session = request.getSession(false) ?: throw ApiException(ErrorCode.UNAUTHORIZED)
        val login = handoff.consume(session) ?: throw ApiException(ErrorCode.UNAUTHORIZED)
        response.setHeader("Cache-Control", "no-store")
        return ApiResponseFactory.success(
            requestId = RequestIds.currentOrCreate(request),
            data = login,
        )
    }
}
