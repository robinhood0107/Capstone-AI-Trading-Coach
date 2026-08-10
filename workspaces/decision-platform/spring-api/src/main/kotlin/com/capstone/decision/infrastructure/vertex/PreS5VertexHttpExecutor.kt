package com.capstone.decision.infrastructure.vertex

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import java.net.URI
import java.time.Clock
import java.time.Duration
import java.time.Instant

internal data class PreS5VertexHttpRequest(
    val endpoint: URI,
    val apiKey: ByteArray,
    val body: ByteArray,
    val timeout: Duration,
    val expiresAt: Instant,
    val attempt: PreS5VertexGenerateContentAttempt,
)

internal data class PreS5VertexHttpResponse(
    val statusCode: Int,
    val body: ByteArray,
)

internal interface PreS5VertexHttpExecutor {
    fun execute(request: PreS5VertexHttpRequest): PreS5VertexHttpResponse
}

/**
 * fixed Vertex Express origin만 호출하는 no-redirect/no-proxy one-shot executor다. API key는 URI나 log가 아닌
 * direct TLS request target에만 잠시 넣고, response는 bounded byte array로만 caller에 전달한다.
 */
@Component
@ConditionalOnProperty(name = ["app.rag-v2.vertex.enabled"], havingValue = "true")
internal class JdkPreS5VertexHttpExecutor(
    private val clock: Clock = Clock.systemUTC(),
    private val transport: PreS5VertexOneShotHttpsTransport = PreS5VertexOneShotHttpsTransport(),
) : PreS5VertexHttpExecutor {
    override fun execute(request: PreS5VertexHttpRequest): PreS5VertexHttpResponse {
        try {
            require(Instant.now(clock).isBefore(request.expiresAt))
            require(request.attempt.lease.expiresAt == request.expiresAt)
            require(request.endpoint.scheme == "https")
            require(request.endpoint.host == "aiplatform.googleapis.com")
            require(request.endpoint.port == -1)
            require(request.endpoint.userInfo == null)
            require(request.endpoint.rawQuery == null && request.endpoint.rawFragment == null)
            require(request.endpoint.rawPath.matches(ENDPOINT_PATH))
            require(request.body.isNotEmpty() && request.body.size <= MAX_REQUEST_BYTES)
            require(request.apiKey.size in MINIMUM_API_KEY_BYTES..MAXIMUM_API_KEY_BYTES)
            require(request.apiKey.all { byte -> byte.toInt().toChar() in API_KEY_CHARACTERS })
            require(request.timeout in MIN_TIMEOUT..MAX_TIMEOUT)
            require(Instant.now(clock).isBefore(request.expiresAt))
            val response =
                transport.execute(
                    PreS5VertexOneShotHttpsRequest(
                        endpoint = request.endpoint,
                        apiKey = request.apiKey,
                        headers =
                            listOf(
                                "Content-Type" to "application/json",
                            ),
                        body = request.body,
                        timeout = request.timeout,
                    ),
                    MAX_RESPONSE_BYTES,
                )
            val body = response.body
            return PreS5VertexHttpResponse(statusCode = response.statusCode, body = body)
        } catch (error: PreS5VertexTransportException) {
            throw error
        } catch (error: InterruptedException) {
            Thread.currentThread().interrupt()
            throw PreS5VertexTransportException()
        } catch (_: Exception) {
            throw PreS5VertexTransportException()
        } finally {
            // request body는 API call 뒤 immutable corpus text를 오래 붙잡지 않도록 즉시 비운다.
            request.body.fill(0)
            request.apiKey.fill(0)
        }
    }

    private companion object {
        val ENDPOINT_PATH =
            Regex("^/v1/publishers/google/models/gemini-3\\.5-flash:generateContent$")
        const val MAX_REQUEST_BYTES = 60_000
        const val MAX_RESPONSE_BYTES = 65_536
        const val MINIMUM_API_KEY_BYTES = 16
        const val MAXIMUM_API_KEY_BYTES = 512
        val API_KEY_CHARACTERS = (('a'..'z') + ('A'..'Z') + ('0'..'9') + listOf('-', '_')).toSet()
        val MIN_TIMEOUT: Duration = Duration.ofSeconds(1)
        val MAX_TIMEOUT: Duration = Duration.ofSeconds(30)
    }
}

internal class PreS5VertexTransportException : RuntimeException()
