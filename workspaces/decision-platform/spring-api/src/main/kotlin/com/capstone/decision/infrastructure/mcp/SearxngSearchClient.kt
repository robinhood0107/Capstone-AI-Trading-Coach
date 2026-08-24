package com.capstone.decision.infrastructure.mcp

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.json.JsonMapper
import java.io.InputStream
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

class S49SearchUnavailableException(
    val failureLeaf: String,
) : RuntimeException(failureLeaf)

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
        val response = client.send(request, HttpResponse.BodyHandlers.ofInputStream())
        val body =
            response.body().use { stream ->
                readBounded(stream, response.headers().firstValueAsLong("Content-Length").orElse(-1L))
            }
        try {
            when (response.statusCode()) {
                403 -> throw S49SearchUnavailableException("S4_9_SEARCH_UNAVAILABLE_ACCESS_DENIED")
                429 -> throw S49SearchUnavailableException("S4_9_SEARCH_UNAVAILABLE_RATE_LIMITED")
            }
            if (response.statusCode() !in 200..299 || body.size !in 1..MAX_RESPONSE_BYTES) {
                throw S49SearchUnavailableException("S4_9_SEARCH_UNAVAILABLE")
            }
            val root = mapper.readTree(body)
            val results = root?.get("results")
            if (results == null || !results.isArray) {
                throw S49SearchUnavailableException("S4_9_SEARCH_UNAVAILABLE")
            }
            val normalized =
                results
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
            if (normalized.isEmpty()) {
                val unresponsive =
                    root
                        .get("unresponsive_engines")
                        ?.toString()
                        .orEmpty()
                        .lowercase()
                val leaf =
                    when {
                        "captcha" in unresponsive -> "S4_9_SEARCH_UNAVAILABLE_CAPTCHA"
                        "429" in unresponsive || "rate" in unresponsive -> "S4_9_SEARCH_UNAVAILABLE_RATE_LIMITED"
                        "403" in unresponsive || "forbidden" in unresponsive -> "S4_9_SEARCH_UNAVAILABLE_ACCESS_DENIED"
                        unresponsive.isNotBlank() && unresponsive != "[]" -> "S4_9_SEARCH_UNAVAILABLE_ALL_ENGINES"
                        else -> "S4_9_SEARCH_UNAVAILABLE_NO_RESULTS"
                    }
                throw S49SearchUnavailableException(leaf)
            }
            return normalized
        } finally {
            body.fill(0)
        }
    }

    internal fun readBounded(
        stream: InputStream,
        declaredLength: Long,
    ): ByteArray {
        if (declaredLength > MAX_RESPONSE_BYTES) {
            throw S49SearchUnavailableException("S4_9_SEARCH_UNAVAILABLE_RESPONSE_TOO_LARGE")
        }
        val body = stream.readNBytes(MAX_RESPONSE_BYTES + 1)
        if (body.size > MAX_RESPONSE_BYTES) {
            body.fill(0)
            throw S49SearchUnavailableException("S4_9_SEARCH_UNAVAILABLE_RESPONSE_TOO_LARGE")
        }
        return body
    }

    private companion object {
        // CAPTCHA 우회나 다중 scraper 연쇄 호출 없이 공식 SearXNG DuckDuckGo 엔진만 best-effort로 사용한다.
        const val ENGINES = "duckduckgo"
        const val MAX_RESULTS = 10
        const val MAX_RESPONSE_BYTES = 1_000_000
    }
}
