package com.capstone.decision.infrastructure.mcp

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.json.JsonMapper
import java.net.Proxy
import java.net.ProxySelector
import java.net.SocketAddress
import java.net.URI
import java.net.URLEncoder
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.charset.StandardCharsets
import java.time.Duration

data class SearxngSearchResult(
    val title: String,
    val url: String,
    val snippet: String,
)

fun interface PublicWebSearchPort {
    fun search(query: String): List<SearxngSearchResult>
}

internal object NoProxySelector : ProxySelector() {
    override fun select(uri: URI?): List<Proxy> = listOf(Proxy.NO_PROXY)

    override fun connectFailed(
        uri: URI?,
        sa: SocketAddress?,
        ioe: java.io.IOException?,
    ) = Unit
}

/** Spring만 접근하는 내부 SearXNG JSON endpoint이며 engine·result count·response bytes를 고정한다. */
@Component
@ConditionalOnProperty(name = ["app.rag-v2.web.enabled"], havingValue = "true")
class SearxngSearchClient(
    private val properties: RagWebToolProperties,
    private val client: HttpClient =
        HttpClient
            .newBuilder()
            .connectTimeout(Duration.ofSeconds(3))
            .proxy(NoProxySelector)
            .build(),
) : PublicWebSearchPort {
    private val mapper =
        JsonMapper
            .builder(
                JsonFactory
                    .builder()
                    .streamReadConstraints(
                        StreamReadConstraints
                            .builder()
                            .maxDocumentLength(MAX_RESPONSE_BYTES.toLong())
                            .maxNestingDepth(12)
                            .build(),
                    ).build(),
            ).build()

    init {
        properties.validate()
    }

    override fun search(query: String): List<SearxngSearchResult> {
        requirePublicWebQuery(query)
        val encoded = URLEncoder.encode(query, StandardCharsets.UTF_8)
        val endpoint =
            URI.create(
                "${properties.searxngBaseUrl.trimEnd('/')}/search?q=$encoded&format=json&engines=$ENGINES&safesearch=1",
            )
        val request =
            HttpRequest
                .newBuilder(endpoint)
                .timeout(Duration.ofSeconds(8))
                .header("Accept", "application/json")
                .GET()
                .build()
        val response = client.send(request, HttpResponse.BodyHandlers.ofByteArray())
        val body = response.body()
        try {
            require(response.statusCode() in 200..299 && body.size in 1..MAX_RESPONSE_BYTES)
            val root = mapper.readTree(body)
            val results = root?.get("results")
            require(results != null && results.isArray)
            return results
                .values()
                .asSequence()
                .take(MAX_RESULTS)
                .mapNotNull { item ->
                    val title =
                        sanitizePublicWebSearchText(
                            item
                                .get("title")
                                ?.takeIf { it.isString }
                                ?.stringValue()
                                ?.trim()
                                .orEmpty(),
                            512,
                        )
                    val rawUrl =
                        item
                            .get("url")
                            ?.takeIf { it.isString }
                            ?.stringValue()
                            ?.trim()
                            .orEmpty()
                    val snippet =
                        sanitizePublicWebSearchText(
                            item
                                .get("content")
                                ?.takeIf { it.isString }
                                ?.stringValue()
                                ?.trim()
                                .orEmpty(),
                            2_000,
                        )
                    runCatching {
                        require(title.isNotBlank() && title.length <= 512)
                        val url = normalizePublicWebSearchUrl(rawUrl)
                        SearxngSearchResult(title, url, snippet)
                    }.getOrNull()
                }.toList()
        } finally {
            body.fill(0)
        }
    }

    private companion object {
        const val ENGINES = "duckduckgo,brave,mojeek,qwant,wikipedia"
        const val MAX_RESULTS = 10
        const val MAX_RESPONSE_BYTES = 1_000_000
    }
}
