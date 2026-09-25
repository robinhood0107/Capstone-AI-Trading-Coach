package com.capstone.decision.infrastructure.vertex

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.json.JsonMapper
import java.security.KeyFactory
import java.security.PrivateKey
import java.security.spec.PKCS8EncodedKeySpec
import java.util.Base64

internal data class PreS5VertexServiceAccountCredential(
    val projectId: String,
    val clientEmail: String,
    val privateKeyId: String,
    val privateKey: PrivateKey,
)

/** Parses the service-account JSON from the single root-env Base64 value; no credential file path is used. */
@Component
@ConditionalOnProperty(name = ["app.rag-v2.vertex.enabled"], havingValue = "true")
internal class PreS5VertexServiceAccountCredentialProvider(
    private val properties: RagV2VertexProperties,
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
                            .maxDocumentLength(MAX_CREDENTIAL_BYTES.toLong())
                            .maxTokenCount(64)
                            .maxStringLength(MAX_CREDENTIAL_BYTES)
                            .maxNameLength(64)
                            .build(),
                    ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                    .build(),
            ).build()

    fun acquire(): PreS5VertexServiceAccountCredential {
        var raw: ByteArray? = null
        var keyBytes: ByteArray? = null
        try {
            val encoded = properties.serviceAccountJsonB64
            require(encoded.length <= MAX_BASE64_CHARS && encoded.isNotBlank())
            val decoded = Base64.getDecoder().decode(encoded)
            raw = decoded
            require(decoded.size in 1..MAX_CREDENTIAL_BYTES)
            require(Base64.getEncoder().encodeToString(decoded) == encoded)
            val document = mapper.readTree(decoded)
            require(document != null && document.isObject)
            require(document.properties().map { it.key }.toSet() == REQUIRED_FIELDS)
            require(document["type"]?.stringValue() == "service_account")
            require(document["token_uri"]?.stringValue() == TOKEN_URI)
            require(document["universe_domain"]?.stringValue() == "googleapis.com")
            val projectId = requireNotNull(document["project_id"]?.stringValue()).also { require(PROJECT_ID.matches(it)) }
            val clientEmail = requireNotNull(document["client_email"]?.stringValue()).also { require(CLIENT_EMAIL.matches(it)) }
            val privateKeyId = requireNotNull(document["private_key_id"]?.stringValue()).also { require(KEY_ID.matches(it)) }
            val privateKeyPem = requireNotNull(document["private_key"]?.stringValue())
            require(privateKeyPem.startsWith(PEM_BEGIN) && privateKeyPem.endsWith(PEM_END))
            keyBytes =
                Base64.getMimeDecoder().decode(
                    privateKeyPem.removePrefix(PEM_BEGIN).removeSuffix(PEM_END),
                )
            require(keyBytes.size in MINIMUM_KEY_BYTES..MAXIMUM_KEY_BYTES)
            val privateKey = KeyFactory.getInstance("RSA").generatePrivate(PKCS8EncodedKeySpec(keyBytes))
            return PreS5VertexServiceAccountCredential(projectId, clientEmail, privateKeyId, privateKey)
        } catch (_: Exception) {
            throw PreS5VertexServiceAccountCredentialException()
        } finally {
            raw?.fill(0)
            keyBytes?.fill(0)
        }
    }

    private companion object {
        const val TOKEN_URI = "https://oauth2.googleapis.com/token"
        const val PEM_BEGIN = "-----BEGIN PRIVATE KEY-----\n"
        const val PEM_END = "\n-----END PRIVATE KEY-----\n"
        const val MAX_CREDENTIAL_BYTES = 32 * 1024
        const val MAX_BASE64_CHARS = ((MAX_CREDENTIAL_BYTES + 2) / 3) * 4
        const val MINIMUM_KEY_BYTES = 1_000
        const val MAXIMUM_KEY_BYTES = 16 * 1024
        val PROJECT_ID = Regex("^[a-z][a-z0-9-]{4,62}[a-z0-9]$")
        val CLIENT_EMAIL = Regex("^[A-Za-z0-9._%+-]{1,128}@[A-Za-z0-9.-]{1,190}\\.iam\\.gserviceaccount\\.com$")
        val KEY_ID = Regex("^[0-9a-f]{16,128}$")
        val REQUIRED_FIELDS =
            setOf(
                "type",
                "project_id",
                "private_key_id",
                "private_key",
                "client_email",
                "client_id",
                "auth_uri",
                "token_uri",
                "auth_provider_x509_cert_url",
                "client_x509_cert_url",
                "universe_domain",
            )
    }
}

internal class PreS5VertexServiceAccountCredentialException : RuntimeException()
