package com.capstone.decision.api.auth

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.infrastructure.security.BrowserOriginPolicy
import com.capstone.decision.infrastructure.security.FullBrowserRefreshCookieService
import com.capstone.decision.infrastructure.security.JwtService
import io.swagger.v3.oas.annotations.Operation
import io.swagger.v3.oas.annotations.security.SecurityRequirements
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.springframework.beans.factory.annotation.Value
import org.springframework.context.annotation.Profile
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

@RestController
@Profile("mars-full")
@RequestMapping("/api/v1/auth")
class FullBrowserRefreshController(
    private val cookies: FullBrowserRefreshCookieService,
    private val jwtService: JwtService,
    @Value("\${MARS_PUBLIC_ORIGIN:}") private val publicOrigin: String,
) {
    @Operation(operationId = "refreshFullBrowserSession")
    @SecurityRequirements
    @PostMapping("/refresh")
    fun refresh(
        request: HttpServletRequest,
        response: HttpServletResponse,
    ): ApiResponse<LoginResponse> {
        if (!BrowserOriginPolicy.allows(publicOrigin, request.getHeader("Origin")) ||
            request.queryString != null ||
            request.contentLengthLong > 0
        ) {
            throw ApiException(ErrorCode.FORBIDDEN)
        }
        val account = cookies.resume(request, response) ?: throw ApiException(ErrorCode.UNAUTHORIZED)
        val issued = jwtService.issue(account)
        response.setHeader("Cache-Control", "no-store")
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            LoginResponse(
                accessToken = issued.token,
                tokenType = "Bearer",
                expiresAt = issued.expiresAt,
                user = LoginUserResponse(account.userId, account.username, account.role),
            ),
        )
    }
}
