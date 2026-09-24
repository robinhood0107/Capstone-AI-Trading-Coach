package com.capstone.decision.infrastructure.security

import jakarta.servlet.http.HttpServletResponse
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.context.annotation.Profile
import org.springframework.core.annotation.Order
import org.springframework.security.config.annotation.web.builders.HttpSecurity
import org.springframework.security.config.http.SessionCreationPolicy
import org.springframework.security.oauth2.client.authentication.OAuth2AuthenticationToken
import org.springframework.security.oauth2.core.oidc.user.OidcUser
import org.springframework.security.web.SecurityFilterChain
import org.springframework.security.web.authentication.AuthenticationSuccessHandler
import java.net.URI
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.HexFormat

@ConfigurationProperties(prefix = "mars.social-login")
data class FullSocialLoginProperties(
    val publicOrigin: String = "",
    val googleAdminSubjectSha256: String = "",
) {
    fun validatedOrigin(): String {
        val uri = URI.create(publicOrigin)
        require(googleAdminSubjectSha256.matches(Regex("^[0-9a-f]{64}$"))) {
            "Full social login requires one Google operator subject hash."
        }
        require(
            uri.scheme == "https" &&
                uri.host != null &&
                uri.userInfo == null &&
                uri.path.isNullOrEmpty() &&
                uri.rawQuery == null &&
                uri.rawFragment == null &&
                publicOrigin == uri.toString(),
        ) { "Full social login requires one exact HTTPS public origin." }
        return publicOrigin
    }

    fun isGoogleAdminSubject(subject: String): Boolean =
        MessageDigest.isEqual(
            HexFormat.of().parseHex(googleAdminSubjectSha256),
            MessageDigest.getInstance("SHA-256").digest(subject.toByteArray(StandardCharsets.UTF_8)),
        )
}

/** OAuth state and nonce live only in this narrow session chain; all other APIs keep Bearer authentication. */
@Configuration
@Profile("mars-full")
@EnableConfigurationProperties(FullSocialLoginProperties::class)
class SocialLoginSecurityConfig {
    @Bean
    fun socialLoginSuccessHandler(
        handoff: SocialLoginHandoff,
        properties: FullSocialLoginProperties,
    ): AuthenticationSuccessHandler {
        val origin = properties.validatedOrigin()
        return AuthenticationSuccessHandler { request, response, authentication ->
            val login = authentication as? OAuth2AuthenticationToken
            val identity = login?.let(::validatedIdentity)
            if (identity == null) {
                request.getSession(false)?.invalidate()
                response.sendError(HttpServletResponse.SC_UNAUTHORIZED)
                return@AuthenticationSuccessHandler
            }
            val session = request.getSession(false)
            if (session == null) {
                response.sendError(HttpServletResponse.SC_UNAUTHORIZED)
                return@AuthenticationSuccessHandler
            }
            try {
                val googleAdmin =
                    identity.provider == GOOGLE_REGISTRATION &&
                        properties.isGoogleAdminSubject(identity.subject)
                handoff.stage(identity.issuer, identity.subject, googleAdmin, session)
            } catch (_: RuntimeException) {
                session.invalidate()
                response.sendError(HttpServletResponse.SC_SERVICE_UNAVAILABLE)
                return@AuthenticationSuccessHandler
            }
            response.sendRedirect("$origin/auth/complete")
        }
    }

    @Bean
    @Order(0)
    fun socialLoginSecurityFilterChain(
        http: HttpSecurity,
        successHandler: AuthenticationSuccessHandler,
    ): SecurityFilterChain =
        http
            .securityMatcher("/api/v1/auth/oidc/**")
            .csrf { it.disable() }
            .sessionManagement { it.sessionCreationPolicy(SessionCreationPolicy.IF_REQUIRED) }
            .authorizeHttpRequests { it.anyRequest().permitAll() }
            .oauth2Login { oauth ->
                oauth
                    .authorizationEndpoint { it.baseUri("/api/v1/auth/oidc/start") }
                    .redirectionEndpoint { it.baseUri("/api/v1/auth/oidc/callback/*") }
                    .successHandler(successHandler)
            }.build()

    private fun validatedIdentity(login: OAuth2AuthenticationToken): VerifiedSocialIdentity? {
        return when (login.authorizedClientRegistrationId) {
            GOOGLE_REGISTRATION -> {
                val token = (login.principal as? OidcUser)?.idToken ?: return null
                val issuer = token.issuer?.toString()
                val subject = token.subject?.takeIf { it.isNotBlank() }
                if (issuer != SocialLoginHandoff.GOOGLE_ISSUER || subject == null) {
                    null
                } else {
                    VerifiedSocialIdentity(GOOGLE_REGISTRATION, issuer, subject)
                }
            }
            KAKAO_REGISTRATION -> {
                val subject =
                    login.principal.attributes["id"]
                        ?.toString()
                        ?.takeIf(KAKAO_SUBJECT::matches)
                subject?.let {
                    VerifiedSocialIdentity(
                        KAKAO_REGISTRATION,
                        SocialLoginHandoff.KAKAO_ISSUER,
                        it,
                    )
                }
            }
            else -> null
        }
    }

    private data class VerifiedSocialIdentity(
        val provider: String,
        val issuer: String,
        val subject: String,
    )

    private companion object {
        const val GOOGLE_REGISTRATION = "google"
        const val KAKAO_REGISTRATION = "kakao"
        val KAKAO_SUBJECT = Regex("^[0-9]{1,32}$")
    }
}
