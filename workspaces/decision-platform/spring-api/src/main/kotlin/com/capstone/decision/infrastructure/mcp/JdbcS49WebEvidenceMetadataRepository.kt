package com.capstone.decision.infrastructure.mcp

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Component
import org.springframework.transaction.PlatformTransactionManager
import org.springframework.transaction.support.TransactionTemplate
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Clock
import java.time.OffsetDateTime
import java.time.ZoneOffset

fun interface S49WebEvidenceMetadataPort {
    fun record(
        ownerUserId: String,
        oauthClientId: String?,
        researchContextId: String,
        canonicalUrl: String,
        title: String,
        contentSha256: String,
    )
}

/** normalized URL/title/hash만 저장하며 web body와 extracted text는 transaction 입력으로 전달하지 않는다. */
@Component
@ConditionalOnProperty(name = ["app.rag-v2.web.enabled"], havingValue = "true")
class JdbcS49WebEvidenceMetadataRepository(
    private val jdbc: NamedParameterJdbcTemplate,
    private val transactionManager: PlatformTransactionManager,
    private val clock: Clock = Clock.systemUTC(),
) : S49WebEvidenceMetadataPort {
    override fun record(
        ownerUserId: String,
        oauthClientId: String?,
        researchContextId: String,
        canonicalUrl: String,
        title: String,
        contentSha256: String,
    ) {
        val now = clock.instant()
        val id = "s49_web_${sha256("$researchContextId|$canonicalUrl|$contentSha256").take(32)}"
        TransactionTemplate(transactionManager).executeWithoutResult {
            jdbc.queryForObject(
                "SELECT set_config('app.actor_user_id', :ownerUserId, true)",
                mapOf("ownerUserId" to ownerUserId),
                String::class.java,
            )
            require(
                jdbc.queryForObject(
                    """
                    SELECT public.record_s4_9_web_evidence_metadata(
                      :evidenceId, :ownerUserId, :clientId, :contextId, :url, :title, NULL,
                      :retrievedAt, :contentHash, :expiresAt
                    ) IS NOT NULL
                    """.trimIndent(),
                    mapOf(
                        "evidenceId" to id,
                        "ownerUserId" to ownerUserId,
                        "clientId" to oauthClientId,
                        "contextId" to researchContextId,
                        "url" to canonicalUrl,
                        "title" to title,
                        "retrievedAt" to OffsetDateTime.ofInstant(now, ZoneOffset.UTC),
                        "contentHash" to contentSha256,
                        "expiresAt" to OffsetDateTime.ofInstant(now.plusSeconds(86_400), ZoneOffset.UTC),
                    ),
                    Boolean::class.java,
                ) == true,
            )
        }
    }

    private fun sha256(value: String): String {
        val bytes = value.toByteArray(StandardCharsets.UTF_8)
        return try {
            MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(java.util.Locale.ROOT, it) }
        } finally {
            bytes.fill(0)
        }
    }
}
