package com.capstone.decision.infrastructure.vertex

import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import java.nio.charset.StandardCharsets

class PreS5VertexApiKeyProviderTest {
    @Test
    fun `provider reads only the dedicated Vertex API key variable`() {
        val requested = mutableListOf<String>()
        val provider =
            PreS5VertexApiKeyProvider.forTest { name ->
                requested += name
                mapOf(
                    "VERTEX_API_KEY" to "AIzaSyVertexOnlyKey_1234567890",
                    "GOOGLE_API_KEY" to "must-not-be-read",
                    "GEMINI_API_KEY" to "must-not-be-read",
                    "GOOGLE_APPLICATION_CREDENTIALS" to "/must-not-be-read.json",
                )[name]
            }

        val key = provider.acquire()

        assertThat(key.toString(StandardCharsets.US_ASCII)).isEqualTo("AIzaSyVertexOnlyKey_1234567890")
        assertThat(requested).containsExactly("VERTEX_API_KEY")
        key.fill(0)
    }

    @Test
    fun `missing malformed or control character key fails closed`() {
        listOf<String?>(null, "too-short", "AIzaSyContains\nLineBreak_1234567890", "AIzaSyContains?Delimiter_1234567890").forEach { value ->
            val provider = PreS5VertexApiKeyProvider.forTest { value }

            assertThatThrownBy { provider.acquire() }
                .isInstanceOf(PreS5VertexApiKeyException::class.java)
        }
    }
}
