package com.capstone.decision.api.auth

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.infrastructure.security.AuthenticatedAccount
import com.capstone.decision.infrastructure.security.FullPasswordAccountService
import com.capstone.decision.infrastructure.security.JwtService
import com.capstone.decision.infrastructure.security.LoginAttemptLimiter
import com.capstone.decision.infrastructure.security.PasswordAccountAlreadyExistsException
import com.capstone.decision.infrastructure.security.SocialLoginHandoff
import io.swagger.v3.oas.annotations.Operation
import io.swagger.v3.oas.annotations.security.SecurityRequirements
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import jakarta.validation.Valid
import jakarta.validation.constraints.Email
import jakarta.validation.constraints.NotBlank
import jakarta.validation.constraints.Size
import org.springframework.beans.factory.ObjectProvider
import org.springframework.context.annotation.Profile
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

/** FULL login: `demo-user` or a password account email. LOCAL keeps [AuthController] for the same path. */
@RestController
@Profile("mars-full")
@RequestMapping("/api/v1/auth")
class FullPasswordAuthController(
    private val accounts: FullPasswordAccountService,
    private val jwtService: JwtService,
    private val loginAttemptLimiter: LoginAttemptLimiter,
) {
    @Operation(operationId = "loginFullPasswordAccount")
    @SecurityRequirements
    @PostMapping("/login")
    fun login(
        @Valid @RequestBody request: FullPasswordLoginRequest,
        servletRequest: HttpServletRequest,
        response: HttpServletResponse,
    ): ApiResponse<LoginResponse> {
        if (!loginAttemptLimiter.tryAcquire(servletRequest.remoteAddr, request.identifier)) {
            throw ApiException(ErrorCode.RATE_LIMITED)
        }
        val account =
            try {
                accounts.login(request.identifier, request.password)
            } catch (error: RuntimeException) {
                loginAttemptLimiter.releaseReservation()
                throw error
            }
        if (account == null) {
            loginAttemptLimiter.recordFailure(servletRequest.remoteAddr, request.identifier)
            throw ApiException(ErrorCode.UNAUTHORIZED, "Invalid identifier or password.")
        }
        loginAttemptLimiter.recordSuccess(servletRequest.remoteAddr, request.identifier)
        response.setHeader("Cache-Control", "no-store")
        return accountLoginResponse(jwtService, account, servletRequest)
    }
}

/** Minimal email signup and adding a password method to the signed-in account. */
@RestController
@Profile("!mars-demo")
@RequestMapping("/api/v1/auth")
class PasswordAccountController(
    private val accounts: FullPasswordAccountService,
    private val jwtService: JwtService,
    private val loginAttemptLimiter: LoginAttemptLimiter,
    private val socialLogin: ObjectProvider<SocialLoginHandoff>,
) {
    @Operation(operationId = "readAuthenticationOptions")
    @SecurityRequirements
    @GetMapping("/options")
    fun options(request: HttpServletRequest): ApiResponse<AuthenticationOptionsResponse> =
        ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            AuthenticationOptionsResponse(
                signup = true,
                providers = if (socialLogin.ifAvailable == null) emptyList() else SocialLoginHandoff.REGISTRATIONS.sorted(),
            ),
        )

    @Operation(operationId = "registerFullPasswordAccount")
    @SecurityRequirements
    @PostMapping("/signup")
    fun signup(
        @Valid @RequestBody request: FullPasswordSignupRequest,
        servletRequest: HttpServletRequest,
        response: HttpServletResponse,
    ): ApiResponse<LoginResponse> {
        if (!loginAttemptLimiter.tryAcquire(servletRequest.remoteAddr, request.email)) {
            throw ApiException(ErrorCode.RATE_LIMITED)
        }
        val account =
            try {
                accounts.register(request.email, request.password)
            } catch (_: PasswordAccountAlreadyExistsException) {
                loginAttemptLimiter.recordFailure(servletRequest.remoteAddr, request.email)
                throw ApiException(ErrorCode.CONFLICT, "An account with that email already exists.")
            } catch (_: IllegalArgumentException) {
                loginAttemptLimiter.releaseReservation()
                throw ApiException(ErrorCode.VALIDATION_ERROR)
            } catch (error: RuntimeException) {
                loginAttemptLimiter.releaseReservation()
                throw error
            }
        loginAttemptLimiter.recordSuccess(servletRequest.remoteAddr, request.email)
        response.setHeader("Cache-Control", "no-store")
        return accountLoginResponse(jwtService, account, servletRequest)
    }

    @Operation(operationId = "addPasswordLoginToAccount")
    @PutMapping("/password")
    fun addPassword(
        @AuthenticationPrincipal principal: AppPrincipal,
        @Valid @RequestBody request: FullPasswordSignupRequest,
        servletRequest: HttpServletRequest,
        response: HttpServletResponse,
    ): ApiResponse<LoginResponse> {
        val account =
            try {
                accounts.addPassword(principal.userId, request.email, request.password)
            } catch (_: PasswordAccountAlreadyExistsException) {
                throw ApiException(ErrorCode.CONFLICT, "This account or email already has a password login.")
            } catch (_: IllegalArgumentException) {
                throw ApiException(ErrorCode.VALIDATION_ERROR)
            }
        response.setHeader("Cache-Control", "no-store")
        return accountLoginResponse(jwtService, account, servletRequest)
    }
}

private fun accountLoginResponse(
    jwtService: JwtService,
    account: AuthenticatedAccount,
    request: HttpServletRequest,
): ApiResponse<LoginResponse> {
    val issued = jwtService.issue(account)
    return ApiResponseFactory.success(
        requestId = RequestIds.currentOrCreate(request),
        data =
            LoginResponse(
                accessToken = issued.token,
                tokenType = "Bearer",
                expiresAt = issued.expiresAt,
                user = LoginUserResponse(account.userId, account.username, account.role),
            ),
    )
}

data class AuthenticationOptionsResponse(
    val signup: Boolean,
    val providers: List<String>,
)

data class FullPasswordLoginRequest(
    @field:NotBlank
    @field:Size(max = 254)
    val identifier: String,
    @field:NotBlank
    @field:Size(max = 1024)
    val password: String,
)

data class FullPasswordSignupRequest(
    @field:NotBlank
    @field:Email
    @field:Size(max = 254)
    val email: String,
    @field:NotBlank
    @field:Size(min = 15, max = 64)
    val password: String,
)
