package com.capstone.decision.infrastructure.security

import com.capstone.decision.api.auth.LoginResponse
import com.capstone.decision.api.auth.LoginUserResponse
import jakarta.servlet.http.HttpSession
import org.springframework.context.annotation.Profile
import org.springframework.stereotype.Service
import java.time.Duration

/** A short-lived, one-use bridge from the validated Google callback to the Bearer API. */
@Service
@Profile("mars-full")
class GoogleOidcHandoff(
    private val sessions: GoogleOidcSessionRepository,
    private val jwtService: JwtService,
    private val jwtProperties: JwtProperties,
) {
    fun stage(
        issuer: String,
        subject: String,
        operatorSubject: Boolean,
        session: HttpSession,
    ) {
        require(issuer == GOOGLE_ISSUER && subject.isNotBlank())
        synchronized(session) {
            require(session.getAttribute(PENDING_KEY) == null)
            val account =
                sessions.createSession(
                    issuer = issuer,
                    subject = subject,
                    operatorSubject = operatorSubject,
                    ttlSeconds = Math.toIntExact(Duration.ofHours(jwtProperties.ttlHours).seconds),
                )
            val issued = jwtService.issue(account)
            session.maxInactiveInterval = 120
            session.setAttribute(
                PENDING_KEY,
                LoginResponse(
                    accessToken = issued.token,
                    tokenType = "Bearer",
                    expiresAt = issued.expiresAt,
                    user = LoginUserResponse(account.userId, account.username, account.role),
                ),
            )
        }
    }

    fun consume(session: HttpSession): LoginResponse? =
        synchronized(session) {
            val response = session.getAttribute(PENDING_KEY) as? LoginResponse
            session.removeAttribute(PENDING_KEY)
            session.invalidate()
            response
        }

    companion object {
        const val GOOGLE_ISSUER = "https://accounts.google.com"
        private const val PENDING_KEY = "mars.oidc.pending-session.v1"
    }
}
