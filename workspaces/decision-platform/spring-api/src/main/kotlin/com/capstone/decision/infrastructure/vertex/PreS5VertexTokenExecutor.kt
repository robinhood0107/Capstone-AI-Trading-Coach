package com.capstone.decision.infrastructure.vertex

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import java.net.URI
import java.nio.charset.StandardCharsets
import java.time.Clock
import java.time.Duration
import java.time.Instant

internal data class PreS5VertexTokenRequest(
    val assertion: ByteArray,
    val timeout: Duration,
    val expiresAt: Instant,
    val attempt: PreS5VertexTokenAttempt,
)

internal data class PreS5VertexTokenResponse(
    val statusCode: Int,
    val body: ByteArray,
)

internal interface PreS5VertexTokenExecutor {
    fun execute(request: PreS5VertexTokenRequest): PreS5VertexTokenResponse
}

/**
 * service-account assertion은 OAuth token endpoint 한 곳에만 one-shot으로 보낸다. Google auth library의
 * ambient proxy/redirect/logging/retry transport를 사용하지 않고, response는 bounded memory에서 즉시 소거한다.
 */
@Component
@ConditionalOnProperty(name = ["app.rag-v2.vertex.enabled"], havingValue = "true")
internal class JdkPreS5VertexTokenExecutor(
    private val clock: Clock = Clock.systemUTC(),
    private val transport: PreS5VertexOneShotHttpsTransport = PreS5VertexOneShotHttpsTransport(),
) : PreS5VertexTokenExecutor {
    override fun execute(request: PreS5VertexTokenRequest): PreS5VertexTokenResponse {
        var form: ByteArray? = null
        try {
            require(Instant.now(clock).isBefore(request.expiresAt))
            require(request.attempt.lease.expiresAt == request.expiresAt)
            require(request.assertion.size in 1..MAX_ASSERTION_BYTES)
            require(request.assertion.all { it.toInt().toChar() in JWT_ASCII })
            require(request.timeout in MIN_TIMEOUT..MAX_TIMEOUT)
            form =
                (
                    "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion=" +
                        request.assertion.toString(StandardCharsets.US_ASCII)
                ).toByteArray(StandardCharsets.US_ASCII)
            require(form.size <= MAX_FORM_BYTES)
            require(Instant.now(clock).isBefore(request.expiresAt))
            val response =
                transport.execute(
                    PreS5VertexOneShotHttpsRequest(
                        endpoint = TOKEN_ENDPOINT,
                        headers =
                            listOf(
                                "Content-Type" to "application/x-www-form-urlencoded",
                                "Accept" to "application/json",
                            ),
                        body = form,
                        timeout = request.timeout,
                    ),
                    MAX_RESPONSE_BYTES,
                )
            val body = response.body
            return PreS5VertexTokenResponse(response.statusCode, body)
        } catch (error: PreS5VertexTokenTransportException) {
            throw error
        } catch (error: InterruptedException) {
            Thread.currentThread().interrupt()
            throw PreS5VertexTokenTransportException()
        } catch (_: Exception) {
            throw PreS5VertexTokenTransportException()
        } finally {
            request.assertion.fill(0)
            form?.fill(0)
        }
    }

    private companion object {
        val TOKEN_ENDPOINT = URI.create("https://oauth2.googleapis.com/token")
        val JWT_ASCII = ('a'..'z') + ('A'..'Z') + ('0'..'9') + listOf('-', '_', '.')
        const val MAX_ASSERTION_BYTES = 16_384
        const val MAX_FORM_BYTES = 17_000
        const val MAX_RESPONSE_BYTES = 16_384
        val MIN_TIMEOUT: Duration = Duration.ofSeconds(1)
        val MAX_TIMEOUT: Duration = Duration.ofSeconds(30)
    }
}

internal class PreS5VertexTokenTransportException : RuntimeException()
