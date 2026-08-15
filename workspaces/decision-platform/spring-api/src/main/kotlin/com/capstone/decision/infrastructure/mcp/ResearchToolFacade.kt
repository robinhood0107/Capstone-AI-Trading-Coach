package com.capstone.decision.infrastructure.mcp

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import java.net.URI
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.concurrent.ConcurrentHashMap

enum class ResearchSourceType {
    GOOGLE_GROUNDING,
    SEARXNG_RESULT,
    USER_ROOT,
    DISCOVERED_LINK,
}

data class RegisteredResearchSource(
    val resultId: String,
    val sourceType: ResearchSourceType,
    val title: String,
    val url: String,
    val snippet: String,
    val parentResultId: String? = null,
    val depth: Int = 0,
)

data class RegisteredWebDocument(
    val source: RegisteredResearchSource,
    val document: BoundedWebDocument,
    val discoveredLinks: List<RegisteredResearchSource>,
)

/** 외부 MCP와 내부 gRPC가 같은 provenance/SSRF 경계를 사용하도록 검색·읽기를 한 facade로 모은다. */
@Component
@ConditionalOnProperty(name = ["app.rag-v2.web.enabled"], havingValue = "true")
class ResearchToolFacade(
    private val searchPort: PublicWebSearchPort,
    private val readerPort: PublicWebReaderPort,
) {
    private val sessions = ConcurrentHashMap<String, MutableMap<String, RegisteredResearchSource>>()

    fun openSession(sessionId: String) {
        require(SESSION_ID.matches(sessionId))
        sessions.putIfAbsent(sessionId, linkedMapOf())
    }

    fun closeSession(sessionId: String) {
        sessions.remove(sessionId)
    }

    fun registerUserRoots(
        sessionId: String,
        question: String,
    ): List<RegisteredResearchSource> {
        val session = requireNotNull(sessions[sessionId])
        return HTTPS_IN_TEXT
            .findAll(question)
            .map { it.value.trimEnd('.', ',', ')', ']', '}', '>', '"', '\'') }
            .mapNotNull(::publicHttpsRootOrNull)
            .distinct()
            .take(MAX_USER_ROOTS)
            .mapIndexed { index, url ->
                RegisteredResearchSource(
                    resultId = resultId("user", url, index),
                    sourceType = ResearchSourceType.USER_ROOT,
                    title = URI.create(url).host,
                    url = url,
                    snippet = "",
                ).also { source -> synchronized(session) { session.putIfAbsent(source.resultId, source) } }
            }.toList()
    }

    fun search(
        sessionId: String,
        query: String,
    ): List<RegisteredResearchSource> {
        requirePublicWebQuery(query)
        val session = requireNotNull(sessions[sessionId])
        val rawResults =
            try {
                searchPort.search(query)
            } catch (error: S49SearchUnavailableException) {
                throw error
            } catch (_: Exception) {
                throw S49SearchUnavailableException("S4_9_SEARCH_UNAVAILABLE")
            }
        if (rawResults.isEmpty()) {
            throw S49SearchUnavailableException("S4_9_SEARCH_UNAVAILABLE_NO_RESULTS")
        }
        val results =
            rawResults.mapIndexed { index, result ->
                val source =
                    RegisteredResearchSource(
                        resultId = resultId("searxng", result.url, index),
                        sourceType = ResearchSourceType.SEARXNG_RESULT,
                        title = result.title,
                        url = result.url,
                        snippet = result.snippet,
                    )
                synchronized(session) { session[source.resultId] = source }
                source
            }
        return results
    }

    fun registerGoogleGrounding(
        sessionId: String,
        resultId: String,
        title: String,
        url: String,
        domain: String,
    ): RegisteredResearchSource {
        val session = requireNotNull(sessions[sessionId])
        require(GOOGLE_RESULT_ID.matches(resultId))
        val uri = URI.create(url)
        require(uri.scheme == "https" && uri.host != null && uri.rawUserInfo == null)
        require(domain.isNotBlank() && domain.length <= 253)
        val source = RegisteredResearchSource(resultId, ResearchSourceType.GOOGLE_GROUNDING, title, url, "")
        synchronized(session) { session.putIfAbsent(resultId, source) }
        return source
    }

    fun read(
        sessionId: String,
        resultId: String?,
        compatibleUrl: String?,
    ): RegisteredWebDocument {
        val source = resolve(sessionId, resultId, compatibleUrl)
        // Google redirect URI는 provider grounding metadata로만 검증하고 자동 GET하지 않는다.
        if (source.sourceType == ResearchSourceType.GOOGLE_GROUNDING) {
            throw S49WebReadRejectedException("S4_9_GOOGLE_GROUNDING_AUTOMATED_READ_FORBIDDEN")
        }
        val document = readerPort.read(source.url)
        val session = requireNotNull(sessions[sessionId])
        val discovered =
            if (source.depth >= MAX_LINK_DEPTH) {
                emptyList()
            } else {
                document.discoveredUrls.take(MAX_LINKS_PER_PAGE).mapIndexed { index, url ->
                    RegisteredResearchSource(
                        resultId = resultId("link", "${source.resultId}:$url", index),
                        sourceType = ResearchSourceType.DISCOVERED_LINK,
                        title = URI.create(url).host,
                        url = url,
                        snippet = "",
                        parentResultId = source.resultId,
                        depth = source.depth + 1,
                    ).also { discoveredSource ->
                        synchronized(session) { session.putIfAbsent(discoveredSource.resultId, discoveredSource) }
                    }
                }
            }
        return RegisteredWebDocument(source, document, discovered)
    }

    fun source(
        sessionId: String,
        resultId: String,
    ): RegisteredResearchSource? = sessions[sessionId]?.let { synchronized(it) { it[resultId] } }

    fun resolve(
        sessionId: String,
        resultId: String?,
        compatibleUrl: String?,
    ): RegisteredResearchSource {
        val session = requireNotNull(sessions[sessionId])
        return synchronized(session) {
            when {
                !resultId.isNullOrBlank() -> session[resultId]
                !compatibleUrl.isNullOrBlank() -> session.values.singleOrNull { it.url == compatibleUrl }
                else -> null
            }
        } ?: throw S49WebReadRejectedException("S4_9_WEB_READ_SOURCE_NOT_REGISTERED")
    }

    private fun resultId(
        prefix: String,
        url: String,
        index: Int,
    ): String = "${prefix}_${sha256("$url:$index").take(24)}"

    private fun sha256(value: String): String {
        val bytes = value.toByteArray(StandardCharsets.UTF_8)
        return try {
            MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
        } finally {
            bytes.fill(0)
        }
    }

    private fun publicHttpsRootOrNull(value: String): String? =
        runCatching {
            val uri = URI.create(value).normalize()
            require(uri.scheme == "https" && uri.host != null && uri.rawUserInfo == null && uri.rawFragment == null)
            require(uri.port in setOf(-1, 443) && value.length <= 2_048)
            uri.toASCIIString()
        }.getOrNull()

    private companion object {
        val SESSION_ID = Regex("^(?:s49_ctx|s49_run)_[0-9a-f]{32}$")
        val GOOGLE_RESULT_ID = Regex("^google_[1-9][0-9]{0,2}$")
        val HTTPS_IN_TEXT = Regex("https://[^\\s<>]+")
        const val MAX_LINK_DEPTH = 3
        const val MAX_LINKS_PER_PAGE = 20
        const val MAX_USER_ROOTS = 5
    }
}
