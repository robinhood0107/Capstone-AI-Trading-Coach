package com.capstone.decision.infrastructure.vertex

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import java.net.URI
import java.time.Clock
import java.time.Duration
import java.time.Instant

internal data class PreS5VertexHttpRequest(
    val endpoint: URI,
    val bearerToken: ByteArray,
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
 * fixed Vertex AI origin만 호출하는 no-redirect/no-proxy one-shot executor다. OAuth bearer는 direct TLS
 * Authorization header에만 잠시 넣고, response는 bounded byte array로만 caller에 전달한다.
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
            require(request.bearerToken.size in MINIMUM_BEARER_TOKEN_BYTES..MAXIMUM_BEARER_TOKEN_BYTES)
            require(request.bearerToken.all { byte -> byte.toInt() in 0x21..0x7e })
            require(request.timeout in MIN_TIMEOUT..MAX_TIMEOUT)
            require(Instant.now(clock).isBefore(request.expiresAt))
            val response =
                transport.execute(
                    PreS5VertexOneShotHttpsRequest(
                        endpoint = request.endpoint,
                        bearerToken = request.bearerToken,
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
            request.bearerToken.fill(0)
        }
    }

    private companion object {
        val ENDPOINT_PATH =
            Regex(
                "^/v1/projects/[a-z][a-z0-9-]{4,62}[a-z0-9]/locations/global/" +
                    "publishers/google/models/[a-z][a-z0-9.-]{2,127}:generateContent$",
            )
        const val MAX_REQUEST_BYTES = 60_000
        const val MAX_RESPONSE_BYTES = 65_536
        const val MINIMUM_BEARER_TOKEN_BYTES = 16
        const val MAXIMUM_BEARER_TOKEN_BYTES = 8 * 1024
        val MIN_TIMEOUT: Duration = Duration.ofSeconds(1)
        val MAX_TIMEOUT: Duration = Duration.ofSeconds(30)
    }
}

internal class PreS5VertexTransportException : RuntimeException()
