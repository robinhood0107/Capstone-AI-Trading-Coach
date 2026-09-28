package com.capstone.decision.infrastructure.security

import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.springframework.beans.factory.ObjectProvider
import org.springframework.beans.factory.annotation.Value
import org.springframework.context.annotation.Profile
import org.springframework.http.ResponseCookie
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Repository
import org.springframework.stereotype.Service
import java.time.Duration
import java.time.OffsetDateTime

@Repository
@Profile("mars-full")
class FullBrowserRefreshSessionRepository(
    private val authDatabaseProvider: ObjectProvider<AuthDatabase>,
) {
    private fun jdbc() =
        JdbcTemplate(
            authDatabaseProvider.ifAvailable?.dataSource
                ?: throw IllegalStateException("Authentication database is unavailable."),
        )

    fun issue(accessSessionHandle: String): String =
        requireNotNull(jdbc().queryForObject("select issue_full_browser_refresh_session_v1(?)", String::class.java, accessSessionHandle))

    fun resume(
        refreshHandle: String,
        accessTtlSeconds: Int,
    ): AuthenticatedAccount? =
        jdbc()
            .query(
                """
                select session_handle,actor_user_id,username,actor_role,actor_security_version,expires_at
                from resume_full_browser_refresh_session_v1(?,?)
                """.trimIndent(),
                { row, _ ->
                    AuthenticatedAccount(
                        row.getString("actor_user_id"),
                        row.getString("username"),
                        DemoRole.valueOf(row.getString("actor_role")),
                        row.getLong("actor_security_version"),
                        row.getString("session_handle"),
                        row.getObject("expires_at", OffsetDateTime::class.java),
                    )
                },
                refreshHandle,
                accessTtlSeconds,
            ).singleOrNull()

    fun revoke(refreshHandle: String): Boolean =
        jdbc().queryForObject("select revoke_full_browser_refresh_session_v1(?)", Boolean::class.java, refreshHandle) == true
}

@Service
@Profile("mars-full")
class FullBrowserRefreshCookieService(
    private val sessions: FullBrowserRefreshSessionRepository,
    jwtProperties: JwtProperties,
    @Value("\${MARS_PUBLIC_ORIGIN:}") private val publicOrigin: String,
) {
    private val accessTtlSeconds = Math.toIntExact(Duration.ofHours(jwtProperties.ttlHours).seconds)

    fun issue(
        accessSessionHandle: String,
        response: HttpServletResponse,
    ) {
        setCookie(response, sessions.issue(accessSessionHandle), MAX_AGE)
    }

    fun resume(
        request: HttpServletRequest,
        response: HttpServletResponse,
    ): AuthenticatedAccount? {
        val handle = cookie(request) ?: return null
        val account = sessions.resume(handle, accessTtlSeconds)
        if (account == null) clear(response) else setCookie(response, handle, MAX_AGE)
        return account
    }

    fun revoke(
        request: HttpServletRequest,
        response: HttpServletResponse,
    ) {
        cookie(request)?.let(sessions::revoke)
        clear(response)
    }

    private fun cookie(request: HttpServletRequest): String? =
        request.cookies
            ?.firstOrNull { it.name == COOKIE_NAME }
            ?.value
            ?.takeIf { it.matches(REFRESH_HANDLE) }

    private fun clear(response: HttpServletResponse) = setCookie(response, "", Duration.ZERO)

    private fun setCookie(
        response: HttpServletResponse,
        value: String,
        maxAge: Duration,
    ) {
        response.addHeader(
            "Set-Cookie",
            ResponseCookie
                .from(COOKIE_NAME, value)
                .httpOnly(true)
                .secure(publicOrigin.startsWith("https://"))
                .sameSite("Lax")
                .path("/api/v1/auth")
                .maxAge(maxAge)
                .build()
                .toString(),
        )
    }

    private companion object {
        const val COOKIE_NAME = "mars_full_refresh"
        val MAX_AGE: Duration = Duration.ofDays(30)
        val REFRESH_HANDLE = Regex("^rfs1_[0-9a-f]{64}$")
    }
}
