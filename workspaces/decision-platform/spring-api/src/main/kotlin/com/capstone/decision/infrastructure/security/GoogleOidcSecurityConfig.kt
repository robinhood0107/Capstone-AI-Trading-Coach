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

@ConfigurationProperties(prefix = "mars.oidc")
data class GoogleOidcProperties(
    val publicOrigin: String = "",
    val adminSubjectSha256: String = "",
) {
    fun validatedOrigin(): String {
        val uri = URI.create(publicOrigin)
        require(adminSubjectSha256.matches(Regex("^[0-9a-f]{64}$"))) {
            "Google OIDC requires one operator subject hash."
        }
        require(
            uri.scheme == "https" &&
                uri.host != null &&
                uri.userInfo == null &&
                uri.path.isNullOrEmpty() &&
                uri.rawQuery == null &&
                uri.rawFragment == null &&
                publicOrigin == uri.toString(),
        ) { "Google OIDC requires one exact HTTPS public origin." }
        return publicOrigin
    }

    fun isOperatorSubject(subject: String): Boolean =
        MessageDigest.isEqual(
            HexFormat.of().parseHex(adminSubjectSha256),
            MessageDigest.getInstance("SHA-256").digest(subject.toByteArray(StandardCharsets.UTF_8)),
        )
}

/** OAuth state and nonce live only in this narrow session chain; all other APIs keep Bearer authentication. */
@Configuration
@Profile("mars-full")
@EnableConfigurationProperties(GoogleOidcProperties::class)
class GoogleOidcSecurityConfig {
    @Bean
    fun googleOidcSuccessHandler(
        handoff: GoogleOidcHandoff,
        properties: GoogleOidcProperties,
    ): AuthenticationSuccessHandler {
        val origin = properties.validatedOrigin()
        return AuthenticationSuccessHandler { request, response, authentication ->
            val login = authentication as? OAuth2AuthenticationToken
            val principal = login?.principal as? OidcUser
            val idToken = principal?.idToken
            val issuer = idToken?.issuer?.toString()
            val subject = idToken?.subject?.takeIf(String::isNotBlank)
            if (
                login?.authorizedClientRegistrationId != "google" ||
                issuer != GoogleOidcHandoff.GOOGLE_ISSUER ||
                subject == null
            ) {
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
                handoff.stage(issuer, subject, properties.isOperatorSubject(subject), session)
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
    fun googleOidcSecurityFilterChain(
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
}
