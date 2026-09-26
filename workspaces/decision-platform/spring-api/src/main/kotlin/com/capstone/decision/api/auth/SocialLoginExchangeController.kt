package com.capstone.decision.api.auth

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.infrastructure.security.FullSocialLoginProperties
import com.capstone.decision.infrastructure.security.SocialLoginHandoff
import io.swagger.v3.oas.annotations.Operation
import io.swagger.v3.oas.annotations.security.SecurityRequirements
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

/** Same-origin, one-use handoff after Spring Security has consumed a provider authorization code. */
@RestController
@ConditionalOnProperty(prefix = "mars.social-login", name = ["enabled"], havingValue = "true")
@RequestMapping("/api/v1/auth/oidc")
class SocialLoginExchangeController(
    private val handoff: SocialLoginHandoff,
    properties: FullSocialLoginProperties,
) {
    private val publicOrigin = properties.validatedOrigin()

    @Operation(operationId = "exchangeSocialLoginSession")
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
