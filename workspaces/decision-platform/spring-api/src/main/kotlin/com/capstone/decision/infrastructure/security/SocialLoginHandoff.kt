package com.capstone.decision.infrastructure.security

import com.capstone.decision.api.auth.LoginResponse
import com.capstone.decision.api.auth.LoginUserResponse
import jakarta.servlet.http.HttpSession
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Service
import java.time.Duration

/** A short-lived, one-use bridge from a validated provider callback to the Bearer API. */
@Service
@ConditionalOnProperty(prefix = "mars.social-login", name = ["enabled"], havingValue = "true")
class SocialLoginHandoff(
    private val sessions: SocialLoginSessionRepository,
    private val jwtService: JwtService,
    private val jwtProperties: JwtProperties,
) {
    fun stage(
        issuer: String,
        subject: String,
        operatorSubject: Boolean,
        session: HttpSession,
    ) {
        require(issuer in ALLOWED_ISSUERS && subject.isNotBlank())
        require(!operatorSubject || issuer == GOOGLE_ISSUER)
        synchronized(session) {
            require(session.getAttribute(PENDING_KEY) == null)
            require(session.getAttribute(LINK_USER_ID_KEY) == null)
            val account =
                sessions.createSession(
                    issuer = issuer,
                    subject = subject,
                    operatorSubject = operatorSubject,
                    ttlSeconds = Math.toIntExact(Duration.ofHours(jwtProperties.ttlHours).seconds),
                )
            stageAccount(account, session)
        }
    }

    fun beginLink(
        userId: String,
        provider: String,
        session: HttpSession,
    ) {
        require(userId.matches(Regex("^usr_[A-Za-z0-9_-]{4,96}$")))
        require(provider in REGISTRATIONS)
        synchronized(session) {
            require(session.getAttribute(PENDING_KEY) == null)
            require(session.getAttribute(LINK_USER_ID_KEY) == null)
            session.maxInactiveInterval = 300
            session.setAttribute(LINK_USER_ID_KEY, userId)
            session.setAttribute(LINK_PROVIDER_KEY, provider)
        }
    }

    fun linkIntent(session: HttpSession): SocialLoginLinkIntent? =
        synchronized(session) {
            val userId = session.getAttribute(LINK_USER_ID_KEY) as? String ?: return@synchronized null
            val provider = session.getAttribute(LINK_PROVIDER_KEY) as? String ?: return@synchronized null
            SocialLoginLinkIntent(userId, provider)
        }

    fun stageLinkedIdentity(
        userId: String,
        issuer: String,
        subject: String,
        operatorSubject: Boolean,
        session: HttpSession,
    ) {
        require(issuer in ALLOWED_ISSUERS && subject.isNotBlank())
        require(!operatorSubject || issuer == GOOGLE_ISSUER)
        synchronized(session) {
            require(session.getAttribute(PENDING_KEY) == null)
            require(session.getAttribute(LINK_USER_ID_KEY) == userId)
            val account =
                sessions.linkIdentityAndCreateSession(
                    userId = userId,
                    issuer = issuer,
                    subject = subject,
                    operatorSubject = operatorSubject,
                    ttlSeconds = Math.toIntExact(Duration.ofHours(jwtProperties.ttlHours).seconds),
                )
            session.removeAttribute(LINK_USER_ID_KEY)
            session.removeAttribute(LINK_PROVIDER_KEY)
            stageAccount(account, session)
        }
    }

    private fun stageAccount(
        account: AuthenticatedAccount,
        session: HttpSession,
    ) {
        synchronized(session) {
            require(session.getAttribute(PENDING_KEY) == null)
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
        const val KAKAO_ISSUER = "https://kauth.kakao.com"
        private val ALLOWED_ISSUERS = setOf(GOOGLE_ISSUER, KAKAO_ISSUER)
        val REGISTRATIONS = setOf("google", "kakao")
        const val LINK_USER_ID_KEY = "mars.social-login.link.user-id.v1"
        const val LINK_PROVIDER_KEY = "mars.social-login.link.provider.v1"
        private const val PENDING_KEY = "mars.social-login.pending-session.v1"
    }
}

data class SocialLoginLinkIntent(
    val userId: String,
    val provider: String,
)
