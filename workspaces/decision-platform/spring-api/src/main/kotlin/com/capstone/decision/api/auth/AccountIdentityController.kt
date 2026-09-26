package com.capstone.decision.api.auth

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.infrastructure.security.FullSocialLoginProperties
import com.capstone.decision.infrastructure.security.SocialLoginHandoff
import com.capstone.decision.infrastructure.security.SocialLoginSessionRepository
import io.swagger.v3.oas.annotations.Operation
import jakarta.servlet.http.HttpServletRequest
import jakarta.validation.constraints.Pattern
import org.springframework.beans.factory.ObjectProvider
import org.springframework.context.annotation.Profile
import org.springframework.dao.DataIntegrityViolationException
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import java.time.OffsetDateTime

/** Provider accounts are linked only by an already authenticated owner, never by matching email. */
@RestController
@Profile("!mars-demo")
@RequestMapping("/api/v1/auth/identities")
class AccountIdentityController(
    private val identities: SocialLoginSessionRepository,
    private val handoffProvider: ObjectProvider<SocialLoginHandoff>,
    private val propertiesProvider: ObjectProvider<FullSocialLoginProperties>,
) {
    @Operation(operationId = "listAccountAuthenticationMethods")
    @GetMapping
    fun list(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<List<AccountAuthenticationMethodResponse>> =
        ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            identities.listAuthenticationMethods(principal.userId).map {
                AccountAuthenticationMethodResponse(it.provider, it.email, it.linkedAt)
            },
        )

    @Operation(operationId = "startAccountProviderLink")
    @PostMapping("/{provider}/link/start")
    fun startLink(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable @Pattern(regexp = "google|kakao") provider: String,
        request: HttpServletRequest,
    ): ApiResponse<ProviderLinkStartResponse> {
        if (provider !in SocialLoginHandoff.REGISTRATIONS) throw ApiException(ErrorCode.VALIDATION_ERROR)
        val handoff = handoffProvider.ifAvailable ?: throw ApiException(ErrorCode.NOT_FOUND)
        val publicOrigin = propertiesProvider.getObject().validatedOrigin()
        if (request.getHeader("Origin") != publicOrigin || request.queryString != null || request.contentLengthLong > 0) {
            throw ApiException(ErrorCode.FORBIDDEN)
        }
        val session = request.getSession(true)
        if (session.getAttribute(SocialLoginHandoff.LINK_USER_ID_KEY) != null) {
            session.invalidate()
            throw ApiException(ErrorCode.CONFLICT)
        }
        request.changeSessionId()
        handoff.beginLink(principal.userId, provider, session)
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            ProviderLinkStartResponse("/api/v1/auth/oidc/start/$provider"),
        )
    }

    @Operation(operationId = "unlinkAccountProvider")
    @DeleteMapping("/{provider}")
    fun unlink(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable @Pattern(regexp = "google|kakao") provider: String,
        request: HttpServletRequest,
    ): ApiResponse<ProviderUnlinkResponse> {
        val issuer =
            when (provider) {
                "google" -> SocialLoginHandoff.GOOGLE_ISSUER
                "kakao" -> SocialLoginHandoff.KAKAO_ISSUER
                else -> throw ApiException(ErrorCode.VALIDATION_ERROR)
            }
        val removed =
            try {
                identities.unlinkIdentity(principal.userId, issuer)
            } catch (_: DataIntegrityViolationException) {
                throw ApiException(ErrorCode.CONFLICT)
            }
        if (!removed) throw ApiException(ErrorCode.NOT_FOUND)
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            ProviderUnlinkResponse(provider, linked = false),
        )
    }
}

data class AccountAuthenticationMethodResponse(
    val provider: String,
    val email: String?,
    val linkedAt: OffsetDateTime,
)

data class ProviderLinkStartResponse(
    val authorizationPath: String,
)

data class ProviderUnlinkResponse(
    val provider: String,
    val linked: Boolean,
)
