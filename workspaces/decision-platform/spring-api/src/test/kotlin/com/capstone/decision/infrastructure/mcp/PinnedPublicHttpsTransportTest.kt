package com.capstone.decision.infrastructure.mcp

import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import java.io.BufferedInputStream
import java.io.ByteArrayInputStream

class PinnedPublicHttpsTransportTest {
    @Test
    fun `bounded long CSP header is accepted without weakening the total header cap`() {
        val response =
            "HTTP/1.1 200 OK\r\n" +
                "content-security-policy: ${"a".repeat(9_000)}\r\n" +
                "accept-ch: \r\n" +
                "content-type: text/plain\r\n" +
                "content-length: 4\r\n\r\n" +
                "safe"

        val parsed = parse(response)

        assertThat(parsed.statusCode).isEqualTo(200)
        assertThat(parsed.body.toString(Charsets.UTF_8)).isEqualTo("safe")
        assertThat(parsed.headers["accept-ch"]).containsExactly("")
    }

    @Test
    fun `empty header value without optional whitespace is accepted`() {
        val response =
            "HTTP/1.1 204 No Content\r\n" +
                "x-empty:\r\n" +
                "content-length: 0\r\n\r\n"

        val parsed = parse(response)

        assertThat(parsed.statusCode).isEqualTo(204)
        assertThat(parsed.headers["x-empty"]).containsExactly("")
    }

    @Test
    fun `aggregate headers above thirty two kibibytes remain rejected`() {
        val response =
            "HTTP/1.1 200 OK\r\n" +
                (1..3).joinToString("") { index -> "x-long-$index: ${"a".repeat(11_000)}\r\n" } +
                "content-length: 0\r\n\r\n"

        assertThatThrownBy { parse(response) }.isInstanceOf(IllegalArgumentException::class.java)
    }

    @Test
    fun `chunk size overflow is rejected before peer sized allocation`() {
        val response =
            "HTTP/1.1 200 OK\r\n" +
                "transfer-encoding: chunked\r\n\r\n" +
                "1\r\na\r\n" +
                "7fffffff\r\n"

        assertThatThrownBy { parse(response) }.isInstanceOf(IllegalArgumentException::class.java)
    }

    @Test
    fun `chunk trailers have aggregate count and byte bounds`() {
        val trailers = (1..33).joinToString("") { "x-trailer-$it: value\r\n" }
        val response =
            "HTTP/1.1 200 OK\r\n" +
                "transfer-encoding: chunked\r\n\r\n" +
                "0\r\n" + trailers + "\r\n"

        assertThatThrownBy { parse(response) }.isInstanceOf(IllegalArgumentException::class.java)
    }

    @Test
    fun `expired absolute deadline rejects before response parsing`() {
        assertThatThrownBy {
            PinnedPublicHttpsTransport().parseResponse(
                BufferedInputStream(ByteArrayInputStream("HTTP/1.1 200 OK\r\n".toByteArray())),
                2_000_000,
                System.nanoTime() - 1,
            )
        }.isInstanceOf(IllegalArgumentException::class.java)
    }

    private fun parse(response: String): PublicHttpsResponse =
        PinnedPublicHttpsTransport().parseResponse(
            BufferedInputStream(ByteArrayInputStream(response.toByteArray(Charsets.US_ASCII))),
            2_000_000,
        )
}
