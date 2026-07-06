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

// S0.3에서는 실제 회원가입 대신 명세의 demo 계정만 토큰 발급 경로로 노출한다.
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
        // 실패한 로그인도 공통 envelope의 UNAUTHORIZED로 흘려 프론트 분기 규칙을 고정한다.
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

// 로그인 DTO에서 빈 값은 controller 진입부에서 400 envelope로 검증한다.
data class LoginRequest(
    @field:NotBlank
    val username: String,
    @field:NotBlank
    val password: String,
)

// 토큰과 사용자 표시 정보를 함께 내려 swagger/manual smoke에서 바로 Authorize할 수 있게 한다.
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
