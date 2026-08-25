package com.capstone.decision.api.auth

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.infrastructure.security.DemoAccountService
import com.capstone.decision.infrastructure.security.DemoRole
import com.capstone.decision.infrastructure.security.JwtService
import com.capstone.decision.infrastructure.security.LoginAttemptLimiter
import io.swagger.v3.oas.annotations.Operation
import io.swagger.v3.oas.annotations.media.Content
import io.swagger.v3.oas.annotations.media.Schema
import io.swagger.v3.oas.annotations.responses.ApiResponses
import io.swagger.v3.oas.annotations.security.SecurityRequirements
import jakarta.servlet.http.HttpServletRequest
import jakarta.validation.Valid
import jakarta.validation.constraints.NotBlank
import jakarta.validation.constraints.Size
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import java.time.OffsetDateTime
import io.swagger.v3.oas.annotations.responses.ApiResponse as OpenApiResponse

// S0.3에서는 실제 회원가입 대신 명세의 demo 계정만 토큰 발급 경로로 노출한다.
@RestController
@RequestMapping("/api/v1/auth")
class AuthController(
    private val demoAccountService: DemoAccountService,
    private val jwtService: JwtService,
    private val loginAttemptLimiter: LoginAttemptLimiter,
) {
    @Operation(
        summary = "데모 사용자 로그인 / Demo user login",
        description = "DB-backed demo credential을 검증하고 internal userId를 subject로 쓰는 Bearer JWT를 발급한다.",
    )
    @SecurityRequirements
    @ApiResponses(
        value = [
            OpenApiResponse(responseCode = "200", description = "인증 성공 / Authenticated"),
            OpenApiResponse(
                responseCode = "400",
                description = "요청 JSON/필드 검증 실패 / Invalid request",
                content = [Content(schema = Schema(implementation = ApiResponse::class))],
            ),
            OpenApiResponse(
                responseCode = "401",
                description = "동일한 login 실패 envelope / Invalid credential",
                content = [Content(schema = Schema(implementation = ApiResponse::class))],
            ),
            OpenApiResponse(
                responseCode = "429",
                description = "로그인 시도 제한 / Login attempt limit",
                content = [Content(schema = Schema(implementation = ApiResponse::class))],
            ),
        ],
    )
    @PostMapping("/login")
    fun login(
        @Valid @RequestBody request: LoginRequest,
        servletRequest: HttpServletRequest,
    ): ApiResponse<LoginResponse> {
        if (!loginAttemptLimiter.tryAcquire(servletRequest.remoteAddr, request.username)) {
            throw ApiException(ErrorCode.RATE_LIMITED)
        }
        // 실패한 로그인도 공통 envelope의 UNAUTHORIZED로 흘려 프론트 분기 규칙을 고정한다.
        val account =
            try {
                demoAccountService.authenticate(
                    username = request.username,
                    password = request.password,
                )
            } catch (exception: RuntimeException) {
                loginAttemptLimiter.releaseReservation()
                throw exception
            }
        if (account == null) {
            loginAttemptLimiter.recordFailure(servletRequest.remoteAddr, request.username)
            throw ApiException(ErrorCode.UNAUTHORIZED, "Invalid username or password.")
        }
        loginAttemptLimiter.recordSuccess(servletRequest.remoteAddr, request.username)
        val issuedToken = jwtService.issue(account)
        return ApiResponseFactory.success(
            requestId = RequestIds.currentOrCreate(servletRequest),
            data =
                LoginResponse(
                    accessToken = issuedToken.token,
                    tokenType = "Bearer",
                    expiresAt = issuedToken.expiresAt,
                    user =
                        LoginUserResponse(
                            userId = account.userId,
                            username = account.username,
                            role = account.role,
                        ),
                ),
        )
    }
}

// 로그인 DTO에서 빈 값은 controller 진입부에서 400 envelope로 검증한다.
data class LoginRequest(
    @field:NotBlank
    @field:Size(max = 128)
    @field:Schema(description = "고정 demo login name", maxLength = 128)
    val username: String,
    @field:NotBlank
    @field:Size(max = 1024)
    @field:Schema(
        description = "저장하거나 기록하지 않는 demo password; 인증 허용 범위는 1..72 UTF-8 bytes",
        format = "password",
        writeOnly = true,
        maxLength = 1024,
    )
    val password: String,
)

// 토큰과 사용자 표시 정보를 함께 내려 swagger/manual smoke에서 바로 Authorize할 수 있게 한다.
data class LoginResponse(
    @field:Schema(description = "HS256 Bearer JWT; sub는 검증된 internal userId")
    val accessToken: String,
    val tokenType: String,
    val expiresAt: OffsetDateTime,
    val user: LoginUserResponse,
)

data class LoginUserResponse(
    @field:Schema(description = "DB users.user_id와 동일한 opaque owner ID")
    val userId: String,
    val username: String,
    val role: DemoRole,
)
