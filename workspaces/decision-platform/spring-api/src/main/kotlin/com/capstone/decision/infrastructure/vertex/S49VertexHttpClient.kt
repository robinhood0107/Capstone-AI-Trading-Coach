package com.capstone.decision.infrastructure.vertex

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import java.net.URI
import java.time.Duration

internal data class S49VertexHttpResponse(
    val statusCode: Int,
    val body: ByteArray,
)

internal fun interface S49VertexHttpClient {
    fun generate(
        endpoint: URI,
        bearerToken: ByteArray,
        body: ByteArray,
        timeout: Duration,
    ): S49VertexHttpResponse
}

/** fixed Vertex origin의 generateContent만 호출하며 redirect·proxy·retry를 만들지 않는다. */
@Component
@ConditionalOnProperty(name = ["app.s4-9.strong-llm.enabled"], havingValue = "true")
internal class OneShotS49VertexHttpClient(
    private val transport: PreS5VertexOneShotHttpsTransport = PreS5VertexOneShotHttpsTransport(),
) : S49VertexHttpClient {
    override fun generate(
        endpoint: URI,
        bearerToken: ByteArray,
        body: ByteArray,
        timeout: Duration,
    ): S49VertexHttpResponse {
        try {
            require(endpoint.scheme == "https" && endpoint.host == "aiplatform.googleapis.com")
            require(endpoint.rawQuery == null && endpoint.rawFragment == null && endpoint.userInfo == null)
            require(endpoint.rawPath.matches(PATH))
            require(body.size in 1..MAX_REQUEST_BYTES)
            require(bearerToken.size in 16..8_192)
            val response =
                transport.execute(
                    PreS5VertexOneShotHttpsRequest(
                        endpoint = endpoint,
                        bearerToken = bearerToken,
                        headers = listOf("Content-Type" to "application/json"),
                        body = body,
                        timeout = timeout,
                    ),
                    MAX_RESPONSE_BYTES,
                )
            return S49VertexHttpResponse(response.statusCode, response.body)
        } finally {
            bearerToken.fill(0)
            body.fill(0)
        }
    }

    private companion object {
        val PATH =
            Regex(
                "^/v1/projects/[a-z][a-z0-9-]{4,62}[a-z0-9]/locations/global/" +
                    "publishers/google/models/[a-z][a-z0-9.-]{2,127}:generateContent$",
            )
        const val MAX_REQUEST_BYTES = 512_000
        const val MAX_RESPONSE_BYTES = 128_000
    }
}
