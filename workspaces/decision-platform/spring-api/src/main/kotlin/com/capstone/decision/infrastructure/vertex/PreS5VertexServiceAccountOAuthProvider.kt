package com.capstone.decision.infrastructure.vertex

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.json.JsonMapper
import java.net.URI
import java.nio.charset.StandardCharsets
import java.security.Signature
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.util.Base64

internal data class PreS5VertexAccessToken(
    val projectId: String,
    val value: ByteArray,
)

internal data class PreS5VertexOAuthTokenRequest(
    val endpoint: URI,
    val body: ByteArray,
    val timeout: Duration,
    val expiresAt: Instant,
    val attempt: PreS5VertexTokenAttempt,
)

internal data class PreS5VertexOAuthTokenResponse(
    val statusCode: Int,
    val body: ByteArray,
)

internal interface PreS5VertexOAuthTokenExecutor {
    fun execute(request: PreS5VertexOAuthTokenRequest): PreS5VertexOAuthTokenResponse
}

/** raw OAuth body나 credential 없이 실패 경계 하나만 운영 로그에 남기는 allowlist다. */
internal enum class PreS5VertexOAuthFailureLeaf {
    CREDENTIAL,
    REQUEST,
    TRANSPORT,
    HTTP_4XX,
    HTTP_5XX,
    HTTP_OTHER,
    OAUTH_INVALID_CLIENT,
    OAUTH_INVALID_GRANT,
    OAUTH_INVALID_REQUEST,
    OAUTH_UNAUTHORIZED_CLIENT,
    OAUTH_UNSUPPORTED_GRANT_TYPE,
    RESPONSE_INVALID,
}

/** OAuth token endpoint도 direct TLS 한 번만 사용하며 redirect, proxy, retry를 제공하지 않는다. */
@Component
@ConditionalOnProperty(name = ["app.rag-v2.vertex.enabled"], havingValue = "true")
internal class JdkPreS5VertexOAuthTokenExecutor(
    private val clock: Clock = Clock.systemUTC(),
    private val transport: PreS5VertexOneShotHttpsTransport = PreS5VertexOneShotHttpsTransport(),
) : PreS5VertexOAuthTokenExecutor {
    override fun execute(request: PreS5VertexOAuthTokenRequest): PreS5VertexOAuthTokenResponse {
        try {
            require(Instant.now(clock).isBefore(request.expiresAt))
            require(request.attempt.lease.expiresAt == request.expiresAt)
            require(request.endpoint == TOKEN_ENDPOINT)
            require(request.body.size in 1..MAX_TOKEN_REQUEST_BYTES)
            val response =
                transport.execute(
                    PreS5VertexOneShotHttpsRequest(
                        endpoint = request.endpoint,
                        headers = listOf("Content-Type" to "application/x-www-form-urlencoded"),
                        body = request.body,
                        timeout = request.timeout,
                    ),
                    MAX_TOKEN_RESPONSE_BYTES,
                )
            return PreS5VertexOAuthTokenResponse(response.statusCode, response.body)
        } catch (error: PreS5VertexOneShotHttpsTransportException) {
            throw PreS5VertexOAuthException(PreS5VertexOAuthFailureLeaf.TRANSPORT)
        } catch (_: Exception) {
            throw PreS5VertexOAuthException(PreS5VertexOAuthFailureLeaf.REQUEST)
        } finally {
            request.body.fill(0)
        }
    }

    private companion object {
        val TOKEN_ENDPOINT: URI = URI.create("https://oauth2.googleapis.com/token")
        const val MAX_TOKEN_REQUEST_BYTES = 16 * 1024
        const val MAX_TOKEN_RESPONSE_BYTES = 16 * 1024
    }
}

/**
 * service-account key로 cloud-platform scope JWT를 local 서명하고 access token을 정확히 한 번 교환한다.
 * credential/JWT/token/provider response는 DB·receipt·log에 저장하지 않고 호출자의 byte buffer로만 전달한다.
 */
@Component
@ConditionalOnProperty(name = ["app.rag-v2.vertex.enabled"], havingValue = "true")
internal class PreS5VertexServiceAccountOAuthProvider(
    private val credentialProvider: PreS5VertexServiceAccountCredentialProvider,
    private val executor: PreS5VertexOAuthTokenExecutor,
    private val clock: Clock = Clock.systemUTC(),
) {
    private val mapper =
        JsonMapper
            .builder(
                JsonFactory
                    .builder()
                    .streamReadConstraints(
                        StreamReadConstraints
                            .builder()
                            .maxNestingDepth(2)
                            .maxDocumentLength(MAX_TOKEN_RESPONSE_BYTES.toLong())
                            .maxTokenCount(16)
                            .maxStringLength(MAX_TOKEN_BYTES)
                            .maxNameLength(32)
                            .build(),
                    ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                    .build(),
            ).build()

    fun acquire(
        activation: PreS5VertexActivation,
        attempt: PreS5VertexTokenAttempt,
    ): PreS5VertexAccessToken {
        var assertion: ByteArray? = null
        var requestBody: ByteArray? = null
        var responseBody: ByteArray? = null
        var failureLeaf = PreS5VertexOAuthFailureLeaf.REQUEST
        try {
            require(activation.authenticationMode == "SERVICE_ACCOUNT_OAUTH")
            require(activation.tokenPhysicalCallCap == 1 && activation.generateContentPhysicalCallCap == 1)
            require(attempt.lease.expiresAt == activation.expiresAt)
            val now = Instant.now(clock)
            require(now.isBefore(activation.expiresAt))
            failureLeaf = PreS5VertexOAuthFailureLeaf.CREDENTIAL
            val credential = credentialProvider.acquire()
            require(credential.projectId == activation.projectId)
            failureLeaf = PreS5VertexOAuthFailureLeaf.REQUEST
            assertion = signedAssertion(credential, now, activation.expiresAt)
            requestBody = GRANT_TYPE_PREFIX.toByteArray(StandardCharsets.US_ASCII) + assertion
            failureLeaf = PreS5VertexOAuthFailureLeaf.TRANSPORT
            val response =
                executor.execute(
                    PreS5VertexOAuthTokenRequest(
                        endpoint = TOKEN_ENDPOINT,
                        body = requestBody,
                        timeout = Duration.ofSeconds(30),
                        expiresAt = activation.expiresAt,
                        attempt = attempt,
                    ),
                )
            responseBody = response.body
            require(responseBody.size in 1..MAX_TOKEN_RESPONSE_BYTES)
            if (response.statusCode !in 200..299) {
                throw PreS5VertexOAuthException(oauthFailureLeaf(response.statusCode, responseBody))
            }
            failureLeaf = PreS5VertexOAuthFailureLeaf.RESPONSE_INVALID
            val root = mapper.readTree(responseBody)
            require(root != null && root.isObject)
            val responseFields = root.properties().map { it.key }.toSet()
            require(responseFields == TOKEN_RESPONSE_FIELDS || responseFields == TOKEN_RESPONSE_FIELDS_WITH_SCOPE)
            require(root["token_type"]?.stringValue() == "Bearer")
            require(root["expires_in"]?.intValue() in 1..3_600)
            root["scope"]?.let { require(it.stringValue() == CLOUD_PLATFORM_SCOPE) }
            val token = requireNotNull(root["access_token"]?.stringValue())
            require(token.length in MIN_TOKEN_BYTES..MAX_TOKEN_BYTES && token.all { it.code in 0x21..0x7e })
            return PreS5VertexAccessToken(credential.projectId, token.toByteArray(StandardCharsets.US_ASCII))
        } catch (error: PreS5VertexOAuthException) {
            throw error
        } catch (_: Exception) {
            throw PreS5VertexOAuthException(failureLeaf)
        } finally {
            assertion?.fill(0)
            requestBody?.fill(0)
            responseBody?.fill(0)
        }
    }

    private fun oauthFailureLeaf(
        statusCode: Int,
        body: ByteArray,
    ): PreS5VertexOAuthFailureLeaf {
        val fallback =
            when (statusCode) {
                in 400..499 -> PreS5VertexOAuthFailureLeaf.HTTP_4XX
                in 500..599 -> PreS5VertexOAuthFailureLeaf.HTTP_5XX
                else -> PreS5VertexOAuthFailureLeaf.HTTP_OTHER
            }
        return runCatching {
            val root = mapper.readTree(body)
            if (root == null || !root.isObject) {
                return@runCatching fallback
            }
            when (root["error"]?.takeIf { it.isString }?.stringValue()) {
                "invalid_client" -> PreS5VertexOAuthFailureLeaf.OAUTH_INVALID_CLIENT
                "invalid_grant" -> PreS5VertexOAuthFailureLeaf.OAUTH_INVALID_GRANT
                "invalid_request" -> PreS5VertexOAuthFailureLeaf.OAUTH_INVALID_REQUEST
                "unauthorized_client" -> PreS5VertexOAuthFailureLeaf.OAUTH_UNAUTHORIZED_CLIENT
                "unsupported_grant_type" -> PreS5VertexOAuthFailureLeaf.OAUTH_UNSUPPORTED_GRANT_TYPE
                else -> fallback
            }
        }.getOrDefault(fallback)
    }

    private fun signedAssertion(
        credential: PreS5VertexServiceAccountCredential,
        now: Instant,
        packetExpiresAt: Instant,
    ): ByteArray {
        val expiry = minOf(now.plusSeconds(300), packetExpiresAt)
        require(expiry.isAfter(now))
        val header = mapper.writeValueAsBytes(mapOf("alg" to "RS256", "kid" to credential.privateKeyId, "typ" to "JWT"))
        val claims =
            mapper.writeValueAsBytes(
                mapOf(
                    "aud" to TOKEN_ENDPOINT.toString(),
                    "exp" to expiry.epochSecond,
                    "iat" to now.epochSecond,
                    "iss" to credential.clientEmail,
                    "scope" to CLOUD_PLATFORM_SCOPE,
                ),
            )
        var signingInput: ByteArray? = null
        var signature: ByteArray? = null
        try {
            val encoder = Base64.getUrlEncoder().withoutPadding()
            val encodedHeader = encoder.encode(header)
            val encodedClaims = encoder.encode(claims)
            signingInput = encodedHeader + byteArrayOf('.'.code.toByte()) + encodedClaims
            signature =
                Signature.getInstance("SHA256withRSA").run {
                    initSign(credential.privateKey)
                    update(signingInput)
                    sign()
                }
            return signingInput + byteArrayOf('.'.code.toByte()) + encoder.encode(signature)
        } finally {
            header.fill(0)
            claims.fill(0)
            signingInput?.fill(0)
            signature?.fill(0)
        }
    }

    private companion object {
        val TOKEN_ENDPOINT: URI = URI.create("https://oauth2.googleapis.com/token")
        const val CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
        const val GRANT_TYPE_PREFIX = "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion="
        const val MAX_TOKEN_RESPONSE_BYTES = 16 * 1024
        const val MIN_TOKEN_BYTES = 16
        const val MAX_TOKEN_BYTES = 8 * 1024
        val TOKEN_RESPONSE_FIELDS = setOf("access_token", "expires_in", "token_type")
        val TOKEN_RESPONSE_FIELDS_WITH_SCOPE = TOKEN_RESPONSE_FIELDS + "scope"
    }
}

internal class PreS5VertexOAuthException(
    val failureLeaf: PreS5VertexOAuthFailureLeaf,
) : RuntimeException()
