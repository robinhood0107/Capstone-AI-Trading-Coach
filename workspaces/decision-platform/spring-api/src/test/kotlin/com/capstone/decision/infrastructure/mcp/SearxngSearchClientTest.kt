package com.capstone.decision.infrastructure.mcp

import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import java.io.ByteArrayInputStream

class SearxngSearchClientTest {
    private val client = SearxngSearchClient(RagWebToolProperties())

    @Test
    fun `streaming response reads only cap plus one byte`() {
        val exact = ByteArray(1_000_000) { 1 }
        assertThat(client.readBounded(ByteArrayInputStream(exact), -1)).hasSize(exact.size)

        assertThatThrownBy {
            client.readBounded(ByteArrayInputStream(ByteArray(1_000_001)), -1)
        }.isInstanceOf(S49SearchUnavailableException::class.java)
            .hasMessage("S4_9_SEARCH_UNAVAILABLE_RESPONSE_TOO_LARGE")
    }

    @Test
    fun `oversized content length fails before body read`() {
        val stream =
            object : ByteArrayInputStream(byteArrayOf(1)) {
                override fun readNBytes(len: Int): ByteArray = error("body must not be read")
            }

        assertThatThrownBy { client.readBounded(stream, 1_000_001) }
            .isInstanceOf(S49SearchUnavailableException::class.java)
    }
}
