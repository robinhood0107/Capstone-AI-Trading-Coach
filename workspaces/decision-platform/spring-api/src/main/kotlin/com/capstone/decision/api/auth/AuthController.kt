package com.capstone.decision.api.auth

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.infrastructure.security.DemoAccountService
import com.capstone.decision.infrastructure.security.JwtService
import jakarta.validation.Valid
import jakarta.validation.constraints.NotBlank
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import java.time.OffsetDateTime

@RestController
@RequestMapping("/api/v1/auth")
class AuthController(
    private val demoAccountService: DemoAccountService,
    private val jwtService: JwtService,
) {
    @PostMapping("/login")
    fun login(
        @Valid @RequestBody request: LoginRequest,
    ): LoginResponse {
        val account =
            demoAccountService.authenticate(
                username = request.username,
                password = request.password,
            ) ?: throw ApiException(ErrorCode.UNAUTHORIZED, "Invalid username or password.")
        val issuedToken = jwtService.issue(account)
        return LoginResponse(
            accessToken = issuedToken.token,
            tokenType = "Bearer",
            expiresAt = issuedToken.expiresAt,
            user =
                LoginUserResponse(
                    userId = account.userId,
                    username = account.username,
                    role = account.role.name,
                ),
        )
    }
}

data class LoginRequest(
    @field:NotBlank
    val username: String,
    @field:NotBlank
    val password: String,
)

data class LoginResponse(
    val accessToken: String,
    val tokenType: String,
    val expiresAt: OffsetDateTime,
    val user: LoginUserResponse,
)

data class LoginUserResponse(
    val userId: String,
    val username: String,
    val role: String,
)
