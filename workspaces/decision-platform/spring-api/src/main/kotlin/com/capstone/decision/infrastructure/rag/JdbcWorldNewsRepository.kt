package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.WorldNewsCollectionStatus
import com.capstone.decision.application.rag.WorldNewsItem
import com.capstone.decision.application.rag.WorldNewsRepository
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Component
import java.time.Instant
import java.time.OffsetDateTime

@Component
class JdbcWorldNewsRepository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
) : WorldNewsRepository {
    override fun lookup(
        query: String,
        asOf: Instant,
        limit: Int,
    ): List<WorldNewsItem> =
        jdbc().query(
            "SELECT * FROM read_world_news_documents_v2(:query,:asOf,:limit)",
            mapOf("query" to query, "asOf" to OffsetDateTime.parse(asOf.toString()), "limit" to limit),
        ) { result, _ ->
            WorldNewsItem(
                documentId = result.getString("document_id"),
                documentVersionId = result.getString("document_version_id"),
                sourceId = result.getString("source_id"),
                provider = result.getString("provider"),
                providerDocumentId = result.getString("provider_document_id"),
                canonicalUrl = result.getString("canonical_url"),
                republicationOfDocumentId = result.getString("republication_of_document_id"),
                identityStatus = result.getString("identity_status"),
                title = result.getString("title"),
                boundedQuote = result.getString("bounded_quote"),
                boundedPassage = result.getString("bounded_passage"),
                language = result.getString("language"),
                publishedAt = result.getObject("published_at", OffsetDateTime::class.java)?.toInstant(),
                publicationStatus = result.getString("publication_status"),
                providerObservedAt = result.getObject("provider_observed_at", OffsetDateTime::class.java).toInstant(),
                firstSeenAt = result.getObject("first_seen_at", OffsetDateTime::class.java).toInstant(),
                availableAt = result.getObject("available_at", OffsetDateTime::class.java).toInstant(),
                rightsProfile = result.getString("rights_profile"),
                externalLlmAllowed = result.getBoolean("external_llm_allowed"),
                lookupAllowed = result.getBoolean("lookup_allowed"),
                ragRetrievalAllowed = result.getBoolean("rag_retrieval_allowed"),
                promptUntrusted = result.getBoolean("prompt_untrusted"),
                collectionStatus = result.getString("collection_status"),
                contentSha256 = result.getString("content_sha256"),
                versionSha256 = result.getString("version_sha256"),
            )
        }

    override fun collectionStatuses(): List<WorldNewsCollectionStatus> =
        jdbc().query("SELECT * FROM read_world_news_collection_status_v2()", emptyMap<String, Any>()) { result, _ ->
            WorldNewsCollectionStatus(
                provider = result.getString("provider"),
                collectionStatus = result.getString("collection_status"),
                startedAt = result.getObject("started_at", OffsetDateTime::class.java).toInstant(),
                completedAt = result.getObject("completed_at", OffsetDateTime::class.java)?.toInstant(),
                observedThrough = result.getObject("observed_through", OffsetDateTime::class.java)?.toInstant(),
                itemCount = result.getInt("item_count"),
                errorCode = result.getString("error_code"),
            )
        }

    private fun jdbc(): NamedParameterJdbcTemplate = jdbcProvider.getIfAvailable() ?: throw IllegalStateException()
}
