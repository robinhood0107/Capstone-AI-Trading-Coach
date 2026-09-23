package com.capstone.decision.api.auth

import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.infrastructure.security.ActorSessionRevocationRepository
import io.swagger.v3.oas.annotations.Operation
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.CacheControl
import org.springframework.http.ResponseEntity
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

/** A browser logout terminates the server-side session as well as clearing local memory. */
@RestController
@RequestMapping("/api/v1/auth")
class AuthLogoutController(
    private val sessions: ActorSessionRevocationRepository,
) {
    @Operation(operationId = "logoutActorSession")
    @PostMapping("/logout")
    fun logout(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ResponseEntity<Void> {
        require(request.queryString == null)
        sessions.revoke(principal.actorRef)
        return ResponseEntity.noContent().cacheControl(CacheControl.noStore()).build()
    }
}
