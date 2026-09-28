package com.capstone.decision.api.auth

import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.infrastructure.security.ActorSessionRevocationRepository
import com.capstone.decision.infrastructure.security.FullBrowserRefreshCookieService
import io.swagger.v3.oas.annotations.Operation
import io.swagger.v3.oas.annotations.responses.ApiResponse
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.springframework.context.annotation.Profile
import org.springframework.http.CacheControl
import org.springframework.http.ResponseEntity
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

/** A browser logout terminates the server-side session as well as clearing local memory. */
@RestController
@Profile("mars-full")
@RequestMapping("/api/v1/auth")
class AuthLogoutController(
    private val sessions: ActorSessionRevocationRepository,
    private val refreshCookies: FullBrowserRefreshCookieService,
) {
    @Operation(operationId = "logoutActorSession")
    @ApiResponse(responseCode = "204", description = "DB actor session revoked")
    @PostMapping("/logout")
    fun logout(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
        response: HttpServletResponse,
    ): ResponseEntity<Void> {
        require(request.queryString == null)
        sessions.revoke(principal.actorRef)
        refreshCookies.revoke(request, response)
        return ResponseEntity.noContent().cacheControl(CacheControl.noStore()).build()
    }
}
