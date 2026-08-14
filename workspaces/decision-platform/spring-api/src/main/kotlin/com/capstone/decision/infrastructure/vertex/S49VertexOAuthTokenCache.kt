package com.capstone.decision.infrastructure.vertex

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.json.JsonMapper
import java.net.URI
import java.nio.charset.StandardCharsets
import java.security.Signature
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.util.Base64

internal data class S49VertexAccessToken(
    val projectId: String,
    val value: ByteArray,
    val expiresAt: Instant,
)

internal fun interface S49VertexAccessTokenProvider {
    fun acquire(): S49VertexAccessToken
}

internal fun interface S49VertexCredentialProvider {
    fun acquire(): PreS5VertexServiceAccountCredential
}

/** S4.9 credential reader는 과거 one-shot packet binding 없이도 동일한 0700/0600 secure loader만 재사용한다. */
@Component
@ConditionalOnProperty(name = ["app.s4-9.strong-llm.enabled"], havingValue = "true")
internal class S49LocalVertexCredentialProvider(
    properties: S49StrongLlmProperties,
) : S49VertexCredentialProvider {
    private val delegate =
        PreS5VertexServiceAccountCredentialProvider(
            RagV2VertexProperties(localRoot = properties.localRoot),
        )

    override fun acquire(): PreS5VertexServiceAccountCredential = delegate.acquire()
}

/** OAuth access token is kept only in memory and refreshed under one synchronized singleflight boundary. */
@Component
@ConditionalOnProperty(name = ["app.s4-9.strong-llm.enabled"], havingValue = "true")
internal class S49VertexOAuthTokenCache(
    private val credentialProvider: S49VertexCredentialProvider,
    private val clock: Clock = Clock.systemUTC(),
    private val transport: PreS5VertexOneShotHttpsTransport = PreS5VertexOneShotHttpsTransport(),
) : S49VertexAccessTokenProvider {
    private val mapper = JsonMapper.builder(JsonFactory.builder().build()).build()
    private var cached: CachedToken? = null

    @Synchronized
    override fun acquire(): S49VertexAccessToken {
        val now = clock.instant()
        cached?.takeIf { it.expiresAt.isAfter(now.plusSeconds(60)) }?.let {
            return S49VertexAccessToken(it.projectId, it.value.copyOf(), it.expiresAt)
        }
        cached?.value?.fill(0)
        cached = null
        val credential = credentialProvider.acquire()
        val assertion = signedAssertion(credential, now)
        val body = GRANT_PREFIX.toByteArray(StandardCharsets.US_ASCII) + assertion
        try {
            val response =
                transport.execute(
                    PreS5VertexOneShotHttpsRequest(
                        endpoint = TOKEN_ENDPOINT,
                        headers = listOf("Content-Type" to "application/x-www-form-urlencoded"),
                        body = body,
                        timeout = Duration.ofSeconds(30),
                    ),
                    16_384,
                )
            try {
                require(response.statusCode in 200..299)
                val root = mapper.readTree(response.body)
                require(root != null && root.isObject && root["token_type"]?.stringValue() == "Bearer")
                val token = requireNotNull(root["access_token"]?.stringValue())
                val ttl = requireNotNull(root["expires_in"]?.intValue()).also { require(it in 1..3_600) }
                require(token.length in 16..8_192)
                val stored = token.toByteArray(StandardCharsets.US_ASCII)
                val expiresAt = now.plusSeconds(ttl.toLong())
                cached = CachedToken(credential.projectId, stored, expiresAt)
                return S49VertexAccessToken(credential.projectId, stored.copyOf(), expiresAt)
            } finally {
                response.body.fill(0)
            }
        } finally {
            assertion.fill(0)
            body.fill(0)
        }
    }

    private fun signedAssertion(
        credential: PreS5VertexServiceAccountCredential,
        now: Instant,
    ): ByteArray {
        val header = mapper.writeValueAsBytes(mapOf("alg" to "RS256", "kid" to credential.privateKeyId, "typ" to "JWT"))
        val claims =
            mapper.writeValueAsBytes(
                mapOf(
                    "aud" to TOKEN_ENDPOINT.toString(),
                    "exp" to now.plusSeconds(300).epochSecond,
                    "iat" to now.epochSecond,
                    "iss" to credential.clientEmail,
                    "scope" to CLOUD_SCOPE,
                ),
            )
        return try {
            val encoder = Base64.getUrlEncoder().withoutPadding()
            val input = encoder.encode(header) + byteArrayOf('.'.code.toByte()) + encoder.encode(claims)
            val signature =
                Signature.getInstance("SHA256withRSA").run {
                    initSign(credential.privateKey)
                    update(input)
                    sign()
                }
            try {
                input + byteArrayOf('.'.code.toByte()) + encoder.encode(signature)
            } finally {
                input.fill(0)
                signature.fill(0)
            }
        } finally {
            header.fill(0)
            claims.fill(0)
        }
    }

    private data class CachedToken(
        val projectId: String,
        val value: ByteArray,
        val expiresAt: Instant,
    )

    private companion object {
        val TOKEN_ENDPOINT = URI.create("https://oauth2.googleapis.com/token")
        const val CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
        const val GRANT_PREFIX = "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion="
    }
}
