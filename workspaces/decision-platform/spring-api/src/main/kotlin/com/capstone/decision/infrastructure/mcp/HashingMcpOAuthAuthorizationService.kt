package com.capstone.decision.infrastructure.mcp

import com.capstone.decision.infrastructure.security.UserSecurityRepository
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.security.oauth2.core.OAuth2RefreshToken
import org.springframework.security.oauth2.core.endpoint.OAuth2AuthorizationRequest
import org.springframework.security.oauth2.server.authorization.InMemoryOAuth2AuthorizationService
import org.springframework.security.oauth2.server.authorization.OAuth2Authorization
import org.springframework.security.oauth2.server.authorization.OAuth2AuthorizationCode
import org.springframework.security.oauth2.server.authorization.OAuth2AuthorizationService
import org.springframework.security.oauth2.server.authorization.OAuth2TokenType
import org.springframework.security.oauth2.server.authorization.client.RegisteredClientRepository
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.HexFormat

/**
 * Spring Authorization Server의 단기 교환 상태는 메모리에 두되 code/refresh 원문은 DB에 쓰지 않는다.
 * DB에는 재사용·회전 감사에 필요한 SHA-256과 owner/client/resource metadata만 forward-only로 남긴다.
 */
internal class HashingMcpOAuthAuthorizationService(
    private val clients: RegisteredClientRepository,
    private val users: UserSecurityRepository,
    private val properties: McpOAuthProperties,
    private val jdbc: NamedParameterJdbcTemplate,
    private val delegate: OAuth2AuthorizationService = InMemoryOAuth2AuthorizationService(),
) : OAuth2AuthorizationService {
    override fun save(authorization: OAuth2Authorization) {
        val client = requireNotNull(clients.findById(authorization.registeredClientId))
        val actor = users.findDemoCredentials().single { it.username == authorization.principalName && it.status == "ACTIVE" }
        authorization.getToken(OAuth2AuthorizationCode::class.java)?.let { codeState ->
            val code = codeState.token
            val request =
                requireNotNull(
                    authorization.getAttribute<OAuth2AuthorizationRequest>(OAuth2AuthorizationRequest::class.java.name),
                )
            val challenge =
                request.additionalParameters["code_challenge"] as? String
                    ?: throw IllegalArgumentException("PKCE S256 challenge is required")
            require(request.additionalParameters["code_challenge_method"] == "S256")
            require(
                jdbc.queryForObject(
                    """
                    SELECT public.upsert_s4_9_mcp_oauth_code_hash(
                      :codeHash, :clientId, :ownerUserId, :securityVersion,
                      :redirectUri, :resourceUri, CAST(:scopes AS text[]), :challenge, :expiresAt
                    ) IS NOT NULL
                    """.trimIndent(),
                    mapOf(
                        "codeHash" to sha256(code.tokenValue),
                        "clientId" to client.clientId,
                        "ownerUserId" to actor.userId,
                        "securityVersion" to actor.securityVersion,
                        "redirectUri" to requireNotNull(request.redirectUri),
                        "resourceUri" to properties.resourceUri,
                        "scopes" to authorization.authorizedScopes.toTypedArray(),
                        "challenge" to challenge,
                        "expiresAt" to OffsetDateTime.ofInstant(requireNotNull(code.expiresAt), ZoneOffset.UTC),
                    ),
                    Boolean::class.java,
                ) == true,
            )
            if (codeState.isInvalidated) {
                require(
                    jdbc.queryForObject(
                        "SELECT public.consume_s4_9_mcp_oauth_code_hash(:codeHash) IS NOT NULL",
                        mapOf("codeHash" to sha256(code.tokenValue)),
                        Boolean::class.java,
                    ) == true,
                )
            }
        }
        authorization.getToken(OAuth2RefreshToken::class.java)?.let { refreshState ->
            val refresh = refreshState.token
            if (refreshState.isInvalidated) {
                revokeRefreshFamily(refresh)
            } else {
                require(
                    jdbc.queryForObject(
                        """
                        SELECT public.rotate_s4_9_mcp_refresh_token_hash(
                          :tokenHash, :clientId, :ownerUserId, :securityVersion,
                          :resourceUri, CAST(:scopes AS text[]), :expiresAt
                        ) IS NOT NULL
                        """.trimIndent(),
                        mapOf(
                            "tokenHash" to sha256(refresh.tokenValue),
                            "clientId" to client.clientId,
                            "ownerUserId" to actor.userId,
                            "securityVersion" to actor.securityVersion,
                            "resourceUri" to properties.resourceUri,
                            "scopes" to authorization.authorizedScopes.toTypedArray(),
                            "expiresAt" to OffsetDateTime.ofInstant(requireNotNull(refresh.expiresAt), ZoneOffset.UTC),
                        ),
                        Boolean::class.java,
                    ) == true,
                )
            }
        }
        delegate.save(authorization)
    }

    override fun remove(authorization: OAuth2Authorization) {
        authorization.getToken(OAuth2RefreshToken::class.java)?.token?.let { refresh ->
            revokeRefreshFamily(refresh)
        }
        delegate.remove(authorization)
    }

    private fun revokeRefreshFamily(refresh: OAuth2RefreshToken) {
        require(
            jdbc.queryForObject(
                "SELECT public.revoke_s4_9_mcp_refresh_token_family(:tokenHash) IS NOT NULL",
                mapOf("tokenHash" to sha256(refresh.tokenValue)),
                Boolean::class.java,
            ) == true,
        )
    }

    override fun findById(id: String): OAuth2Authorization? = delegate.findById(id)

    override fun findByToken(
        token: String,
        tokenType: OAuth2TokenType?,
    ): OAuth2Authorization? = delegate.findByToken(token, tokenType)

    private fun sha256(value: String): String {
        val bytes = value.toByteArray(StandardCharsets.US_ASCII)
        return try {
            HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes))
        } finally {
            bytes.fill(0)
        }
    }
}
