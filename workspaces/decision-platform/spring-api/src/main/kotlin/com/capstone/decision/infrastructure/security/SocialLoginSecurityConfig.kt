package com.capstone.decision.infrastructure.security

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.core.annotation.Order
import org.springframework.dao.DataIntegrityViolationException
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
    val requireGoogleAdminSubject: Boolean = false,
    val enabled: Boolean = false,
) {
    fun validatedOrigin(): String {
        val uri = URI.create(publicOrigin)
        require(
            googleAdminSubjectSha256.matches(ADMIN_SUBJECT_HASH) ||
                (!requireGoogleAdminSubject && googleAdminSubjectSha256.isEmpty()),
        ) {
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
        googleAdminSubjectSha256.matches(ADMIN_SUBJECT_HASH) &&
            MessageDigest.isEqual(
                HexFormat.of().parseHex(googleAdminSubjectSha256),
                MessageDigest.getInstance("SHA-256").digest(subject.toByteArray(StandardCharsets.UTF_8)),
            )

    private companion object {
        val ADMIN_SUBJECT_HASH = Regex("^[0-9a-f]{64}$")
    }
}

/** OAuth state and nonce live only in this narrow session chain; all other APIs keep Bearer authentication. */
@Configuration
@ConditionalOnProperty(prefix = "mars.social-login", name = ["enabled"], havingValue = "true")
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
                response.sendRedirect("$origin/auth/complete?error=provider")
                return@AuthenticationSuccessHandler
            }
            val session = request.getSession(false)
            if (session == null) {
                response.sendRedirect("$origin/auth/complete?error=provider")
                return@AuthenticationSuccessHandler
            }
            try {
                val googleAdmin =
                    identity.provider == GOOGLE_REGISTRATION &&
                        properties.isGoogleAdminSubject(identity.subject)
                val linkIntent = handoff.linkIntent(session)
                if (linkIntent != null) {
                    if (linkIntent.provider != identity.provider) {
                        session.invalidate()
                        response.sendRedirect("$origin/auth/complete?error=provider-link")
                        return@AuthenticationSuccessHandler
                    }
                    handoff.stageLinkedIdentity(
                        userId = linkIntent.userId,
                        issuer = identity.issuer,
                        subject = identity.subject,
                        operatorSubject = googleAdmin,
                        session = session,
                    )
                    response.sendRedirect("$origin/auth/complete?returnTo=%2Fsettings")
                    return@AuthenticationSuccessHandler
                }
                handoff.stage(identity.issuer, identity.subject, googleAdmin, session)
            } catch (_: DataIntegrityViolationException) {
                session.invalidate()
                response.sendRedirect("$origin/auth/complete?error=provider-link")
                return@AuthenticationSuccessHandler
            } catch (_: RuntimeException) {
                session.invalidate()
                response.sendRedirect("$origin/auth/complete?error=provider")
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
        properties: FullSocialLoginProperties,
    ): SecurityFilterChain {
        val origin = properties.validatedOrigin()
        return http
            .securityMatcher("/api/v1/auth/oidc/**")
            .csrf { it.disable() }
            .sessionManagement { it.sessionCreationPolicy(SessionCreationPolicy.IF_REQUIRED) }
            .authorizeHttpRequests { it.anyRequest().permitAll() }
            .oauth2Login { oauth ->
                oauth
                    .authorizationEndpoint { it.baseUri("/api/v1/auth/oidc/start") }
                    .redirectionEndpoint { it.baseUri("/api/v1/auth/oidc/callback/*") }
                    .successHandler(successHandler)
                    .failureHandler { request, response, _ ->
                        request.getSession(false)?.invalidate()
                        response.sendRedirect("$origin/auth/complete?error=provider")
                    }
            }.build()
    }

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
