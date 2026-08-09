package com.capstone.decision.infrastructure.vertex

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import java.nio.charset.StandardCharsets

/**
 * active v2는 `VERTEX_API_KEY`만 읽는 Vertex Express API-key 경계다. Gemini Developer API 변수,
 * ADC, service-account file, ambient credential은 이 provider가 조회하지 않으며 값은 ledger나 log에 남기지 않는다.
 */
@Component
@ConditionalOnProperty(name = ["app.rag-v2.vertex.enabled"], havingValue = "true")
internal class PreS5VertexApiKeyProvider {
    private var environment: (String) -> String? = { name -> System.getenv(name) }

    /** 호출마다 새 byte buffer를 만들고 caller가 one-shot request 완료 직후 소거한다. */
    fun acquire(): ByteArray =
        try {
            val value = environment(ENVIRONMENT_VARIABLE)
            require(value != null && value.length in MINIMUM_KEY_LENGTH..MAXIMUM_KEY_LENGTH)
            require(value.all { it in API_KEY_CHARACTERS })
            value.toByteArray(StandardCharsets.US_ASCII)
        } catch (_: Exception) {
            throw PreS5VertexApiKeyException()
        }

    internal companion object {
        fun forTest(environment: (String) -> String?): PreS5VertexApiKeyProvider =
            PreS5VertexApiKeyProvider().apply { this.environment = environment }

        const val ENVIRONMENT_VARIABLE = "VERTEX_API_KEY"
        const val MINIMUM_KEY_LENGTH = 16
        const val MAXIMUM_KEY_LENGTH = 512
        private val API_KEY_CHARACTERS = (('a'..'z') + ('A'..'Z') + ('0'..'9') + listOf('-', '_')).toSet()
    }
}

internal class PreS5VertexApiKeyException : RuntimeException()
