package com.capstone.decision.infrastructure.mcp

import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityRolePolicy
import com.capstone.decision.infrastructure.security.ActorRlsScope
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
import org.springframework.transaction.annotation.Transactional
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
    private val refreshClaims: McpRefreshClaimContext,
    private val actorRlsScope: ActorRlsScope,
    private val delegate: OAuth2AuthorizationService = InMemoryOAuth2AuthorizationService(),
) : OAuth2AuthorizationService {
    @Transactional
    override fun save(authorization: OAuth2Authorization) {
        try {
            saveBound(authorization)
        } finally {
            refreshClaims.clear()
        }
    }

    private fun saveBound(authorization: OAuth2Authorization) {
        val client = requireNotNull(clients.findById(authorization.registeredClientId))
        val actor = users.findDemoCredentials().single { it.username == authorization.principalName && it.status == "ACTIVE" }
        val refreshClaim = refreshClaims.optional()
        if (refreshClaim != null) {
            require(
                refreshClaim.clientId == client.clientId &&
                    refreshClaim.ownerUserId == actor.userId &&
                    refreshClaim.resourceUri == properties.resourceUri &&
                    refreshClaim.scopes == authorization.authorizedScopes,
            )
        }
        var consumedCodeHash: String? = null
        authorization.getToken(OAuth2AuthorizationCode::class.java)?.let { codeState ->
            val code = codeState.token
            val codeHash = sha256(code.tokenValue)
            val request =
                requireNotNull(
                    authorization.getAttribute<OAuth2AuthorizationRequest>(OAuth2AuthorizationRequest::class.java.name),
                )
            val challenge =
                request.additionalParameters["code_challenge"] as? String
                    ?: throw IllegalArgumentException("PKCE S256 challenge is required")
            require(request.additionalParameters["code_challenge_method"] == "S256")
            if (codeState.isInvalidated) {
                require(
                    jdbc.queryForObject(
                        "SELECT public.consume_s4_9_mcp_oauth_code_hash(:codeHash) IS NOT NULL",
                        mapOf("codeHash" to codeHash),
                        Boolean::class.java,
                    ) == true,
                )
                consumedCodeHash = codeHash
            } else {
                val redirectUri = requireNotNull(request.redirectUri)
                val scopes = authorization.authorizedScopes.sorted()
                actorRlsScope.open(
                    jdbc,
                    actor.userId,
                    ActorCapabilityBinding.request(
                        "ISSUE_MCP_OAUTH_CODE",
                        "OAUTH_CODE",
                        codeHash,
                        ActorCapabilityRolePolicy.OWNER,
                        actor.userId,
                        client.clientId,
                        actor.securityVersion.toString(),
                        redirectUri,
                        properties.resourceUri,
                        scopes.joinToString(","),
                        challenge,
                    ),
                )
                require(
                    jdbc.queryForObject(
                        """
                        SELECT public.upsert_s4_9_mcp_oauth_code_hash(
                          :codeHash, :clientId, :ownerUserId, :securityVersion,
                          :redirectUri, :resourceUri, CAST(:scopes AS text[]), :challenge, :expiresAt
                        ) IS NOT NULL
                        """.trimIndent(),
                        mapOf(
                            "codeHash" to codeHash,
                            "clientId" to client.clientId,
                            "ownerUserId" to actor.userId,
                            "securityVersion" to actor.securityVersion,
                            "redirectUri" to redirectUri,
                            "resourceUri" to properties.resourceUri,
                            "scopes" to scopes.toTypedArray(),
                            "challenge" to challenge,
                            "expiresAt" to OffsetDateTime.ofInstant(requireNotNull(code.expiresAt), ZoneOffset.UTC),
                        ),
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
                val bindingTokenHash = refreshClaim?.tokenHash ?: requireNotNull(consumedCodeHash)
                require(
                    jdbc.queryForObject(
                        """
                        SELECT public.rotate_s4_9_mcp_refresh_token_hash(
                          :tokenHash, :bindingTokenHash, :resourceUri,
                          CAST(:scopes AS text[]), :expiresAt
                        ) IS NOT NULL
                        """.trimIndent(),
                        mapOf(
                            "tokenHash" to sha256(refresh.tokenValue),
                            "bindingTokenHash" to bindingTokenHash,
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
    ): OAuth2Authorization? {
        val authorization = delegate.findByToken(token, tokenType) ?: return null
        val refresh = authorization.getToken(OAuth2RefreshToken::class.java)?.token
        if (refresh?.tokenValue != token || (tokenType != null && tokenType.value != REFRESH_TOKEN_TYPE)) {
            return authorization
        }
        refreshClaims.clear()
        val client = clients.findById(authorization.registeredClientId) ?: return null
        val actor =
            users.findDemoCredentials().singleOrNull {
                it.username == authorization.principalName && it.status == "ACTIVE"
            } ?: return null
        val claimed =
            jdbc
                .query(
                    "SELECT * FROM public.consume_s4_9_mcp_refresh_token(:tokenHash)",
                    mapOf("tokenHash" to sha256(token)),
                ) { result, _ ->
                    McpRefreshClaim(
                        tokenHash = sha256(token),
                        clientId = result.getString("client_id"),
                        ownerUserId = result.getString("owner_user_id"),
                        securityVersion = result.getLong("security_version"),
                        resourceUri = result.getString("resource_uri"),
                        scopes = (result.getArray("scopes").array as Array<*>).map { it.toString() }.toSet(),
                    )
                }.singleOrNull() ?: return null
        val valid =
            claimed.clientId == client.clientId &&
                claimed.ownerUserId == actor.userId &&
                claimed.securityVersion == actor.securityVersion &&
                claimed.resourceUri == properties.resourceUri &&
                claimed.scopes == authorization.authorizedScopes
        if (!valid) return null
        refreshClaims.bind(claimed)
        return authorization
    }

    private companion object {
        const val REFRESH_TOKEN_TYPE = "refresh_token"
    }

    private fun sha256(value: String): String {
        val bytes = value.toByteArray(StandardCharsets.US_ASCII)
        return try {
            HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes))
        } finally {
            bytes.fill(0)
        }
    }
}
