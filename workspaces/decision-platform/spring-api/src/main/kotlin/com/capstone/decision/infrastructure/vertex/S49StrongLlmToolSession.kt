package com.capstone.decision.infrastructure.vertex

import com.capstone.decision.application.rag.RagV2VertexEvidence
import com.capstone.decision.infrastructure.mcp.BoundedWebDocument
import com.capstone.decision.infrastructure.mcp.PublicWebReaderPort
import com.capstone.decision.infrastructure.mcp.PublicWebSearchPort
import com.capstone.decision.infrastructure.mcp.RagToolBudget
import com.capstone.decision.infrastructure.mcp.S49WebReadRejectedException
import com.capstone.decision.infrastructure.mcp.requirePublicWebQuery
import tools.jackson.databind.JsonNode
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.concurrent.ExecutionException
import java.util.concurrent.Executors

internal data class S49ToolExecution(
    val response: Map<String, Any>,
    val updatedEvidence: List<RagV2VertexEvidence>,
)

/** 한 generation request 안에서만 살아 있는 tool session이며 search URL 외 raw result를 영속 저장하지 않는다. */
internal class S49StrongLlmToolSession(
    evidence: List<RagV2VertexEvidence>,
    private val budget: RagToolBudget,
    private val searchClient: PublicWebSearchPort,
    private val webReader: PublicWebReaderPort,
) {
    private val evidence = evidence.toMutableList()
    private val searchableUrls = mutableSetOf<String>()
    var searchCount: Int = 0
        private set
    var readCount: Int = 0
        private set

    fun execute(
        name: String,
        args: JsonNode,
    ): S49ToolExecution =
        when (name) {
            "capstone_web_search" -> search(args)
            "capstone_web_read" -> read(args)
            else -> throw S49StrongLlmToolException("TOOL_NAME")
        }

    fun executeReadBatch(arguments: List<JsonNode>): List<S49ToolExecution> {
        toolRequire(arguments.isNotEmpty() && arguments.size <= budget.maxParallelReads, "TOOL_READ_CARDINALITY")
        toolRequire(readCount + arguments.size <= budget.maxReads, "TOOL_READ_BUDGET")
        val urls =
            arguments.map { args ->
                toolRequire(args.isObject && args.properties().map { it.key }.toSet() == setOf("url"), "TOOL_READ_ARGS")
                args["url"]
                    ?.takeIf { it.isString }
                    ?.stringValue()
                    .orEmpty()
                    .also { toolRequire(it in searchableUrls, "TOOL_READ_URL_NOT_FROM_SEARCH") }
            }
        val documents =
            Executors.newVirtualThreadPerTaskExecutor().use { executor ->
                urls
                    .map { url -> executor.submit<BoundedWebDocument> { webReader.read(url) } }
                    .map { future ->
                        try {
                            future.get()
                        } catch (error: ExecutionException) {
                            val cause = error.cause
                            if (cause is S49WebReadRejectedException) {
                                val leaf = cause.message.orEmpty()
                                throw S49StrongLlmToolException(
                                    leaf.takeIf(TOOL_LEAF::matches) ?: "TOOL_READ_REJECTED",
                                )
                            }
                            throw S49StrongLlmToolException("TOOL_READ_TRANSPORT")
                        }
                    }
            }
        val retained = evidence.take((5 - documents.size).coerceAtLeast(0)).toMutableList()
        val responses =
            documents.mapIndexed { index, document ->
                val hash = sha256(document.text)
                val ordinal = retained.size + index + 1
                retained += RagV2VertexEvidence(ordinal, "cit_$ordinal", "rag_v2_chk_${hash.take(32)}", document.text, hash)
                S49ToolExecution(
                    mapOf(
                        "citationId" to "cit_$ordinal",
                        "canonicalUrl" to document.canonicalUrl,
                        "title" to document.title,
                        "text" to document.text,
                        "contentSha256" to hash,
                        "untrustedData" to true,
                    ),
                    emptyList(),
                )
            }
        evidence.clear()
        evidence.addAll(retained)
        readCount += documents.size
        return responses.map { execution ->
            execution.copy(
                response =
                    execution.response +
                        mapOf(
                            "remainingSearches" to (budget.maxSearches - searchCount),
                            "remainingReads" to (budget.maxReads - readCount),
                        ),
                updatedEvidence = evidence(),
            )
        }
    }

    fun evidence(): List<RagV2VertexEvidence> = evidence.toList()

    private fun search(args: JsonNode): S49ToolExecution {
        toolRequire(args.isObject && args.properties().map { it.key }.toSet() == setOf("query"), "TOOL_SEARCH_ARGS")
        toolRequire(searchCount < budget.maxSearches, "TOOL_SEARCH_BUDGET")
        val query = args["query"]?.takeIf { it.isString }?.stringValue().orEmpty()
        try {
            requirePublicWebQuery(query)
        } catch (_: IllegalArgumentException) {
            throw S49StrongLlmToolException("TOOL_SEARCH_QUERY")
        }
        val results =
            try {
                searchClient.search(query)
            } catch (_: Exception) {
                throw S49StrongLlmToolException("TOOL_SEARCH_TRANSPORT")
            }
        searchableUrls.addAll(results.map { it.url })
        searchCount += 1
        return S49ToolExecution(
            mapOf(
                "results" to
                    results.map { mapOf("title" to it.title, "url" to it.url, "snippet" to it.snippet) },
                "remainingSearches" to (budget.maxSearches - searchCount),
                "remainingReads" to (budget.maxReads - readCount),
                "untrustedData" to true,
            ),
            evidence(),
        )
    }

    private fun read(args: JsonNode): S49ToolExecution = executeReadBatch(listOf(args)).single()

    private fun sha256(value: String): String {
        val bytes = value.toByteArray(StandardCharsets.UTF_8)
        return try {
            MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
        } finally {
            bytes.fill(0)
        }
    }

    private companion object {
        val TOOL_LEAF = Regex("^S4_9_WEB_READ_[A-Z_]+$")
    }
}

internal class S49StrongLlmToolException(
    val leaf: String,
) : IllegalArgumentException(leaf)

private fun toolRequire(
    condition: Boolean,
    leaf: String,
) {
    if (!condition) throw S49StrongLlmToolException(leaf)
}

internal fun s49VertexFunctionDeclarations(): List<Map<String, Any>> =
    listOf(
        mapOf(
            "name" to "capstone_web_search",
            "description" to "Search public sources through the internal SearXNG service when local evidence is insufficient.",
            "parameters" to
                mapOf(
                    "type" to "OBJECT",
                    "properties" to mapOf("query" to mapOf("type" to "STRING")),
                    "required" to listOf("query"),
                ),
        ),
        mapOf(
            "name" to "capstone_web_read",
            "description" to "Read one exact HTTPS URL returned by capstone_web_search as bounded untrusted evidence.",
            "parameters" to
                mapOf(
                    "type" to "OBJECT",
                    "properties" to mapOf("url" to mapOf("type" to "STRING")),
                    "required" to listOf("url"),
                ),
        ),
    )
