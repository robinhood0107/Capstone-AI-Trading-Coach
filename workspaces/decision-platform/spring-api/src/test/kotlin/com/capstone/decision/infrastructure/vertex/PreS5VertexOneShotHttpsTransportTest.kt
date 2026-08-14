package com.capstone.decision.infrastructure.vertex

import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import java.io.IOException
import java.net.URI
import java.nio.charset.StandardCharsets
import java.time.Duration

class PreS5VertexOneShotHttpsTransportTest {
    @Test
    fun `one shot transport writes the OAuth token only in the direct Authorization header`() {
        val channel =
            RecordingChannel(
                "HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Type: application/json\r\n\r\n{}"
                    .toByteArray(StandardCharsets.US_ASCII),
            )
        val factory = RecordingFactory(channel)
        val response =
            PreS5VertexOneShotHttpsTransport(factory).execute(
                request(),
                maximumResponseBytes = 16,
            )

        assertThat(response.statusCode).isEqualTo(200)
        assertThat(response.body).isEqualTo("{}".toByteArray(StandardCharsets.US_ASCII))
        assertThat(factory.openCount).isEqualTo(1)
        assertThat(channel.writeCount).isEqualTo(1)
        assertThat(channel.written.toString(StandardCharsets.US_ASCII)).contains(
            "POST /v1/projects/project-test-123/locations/global/publishers/google/models/gemini-3.5-flash:generateContent HTTP/1.1\r\nHost: aiplatform.googleapis.com\r\nConnection: close\r\nContent-Length: 7\r\nAuthorization: Bearer ya29.vertex-token-test\r\n",
        )
        assertThat(channel.written.toString(StandardCharsets.US_ASCII)).doesNotContain("?key=")
        assertThat(channel.closed).isTrue()
    }

    @Test
    fun `one shot transport never opens a replacement socket or replays the body after a write failure`() {
        val channel = RecordingChannel(ByteArray(0), failWrite = true)
        val factory = RecordingFactory(channel)

        assertThatThrownBy {
            PreS5VertexOneShotHttpsTransport(factory).execute(request(), maximumResponseBytes = 16)
        }.isInstanceOf(PreS5VertexOneShotHttpsTransportException::class.java)

        assertThat(factory.openCount).isEqualTo(1)
        assertThat(channel.writeCount).isEqualTo(1)
        assertThat(channel.closed).isTrue()
    }

    @Test
    fun `one shot transport accepts repeated non framing response metadata`() {
        val channel =
            RecordingChannel(
                (
                    "HTTP/1.1 200 OK\r\n" +
                        "Vary: Origin\r\n" +
                        "Vary: X-Origin\r\n" +
                        "Content-Length: 2\r\n\r\n{}"
                ).toByteArray(StandardCharsets.US_ASCII),
            )

        val response =
            PreS5VertexOneShotHttpsTransport(RecordingFactory(channel)).execute(
                request(),
                maximumResponseBytes = 16,
            )

        assertThat(response.statusCode).isEqualTo(200)
        assertThat(response.body).isEqualTo("{}".toByteArray(StandardCharsets.US_ASCII))
    }

    @Test
    fun `one shot transport rejects repeated content framing headers`() {
        val channel =
            RecordingChannel(
                (
                    "HTTP/1.1 200 OK\r\n" +
                        "Content-Length: 2\r\n" +
                        "Content-Length: 2\r\n\r\n{}"
                ).toByteArray(StandardCharsets.US_ASCII),
            )

        assertThatThrownBy {
            PreS5VertexOneShotHttpsTransport(RecordingFactory(channel)).execute(
                request(),
                maximumResponseBytes = 16,
            )
        }.isInstanceOf(PreS5VertexOneShotHttpsTransportException::class.java)
    }

    private fun request(): PreS5VertexOneShotHttpsRequest =
        PreS5VertexOneShotHttpsRequest(
            endpoint =
                URI.create(
                    "https://aiplatform.googleapis.com/v1/projects/project-test-123/locations/global/" +
                        "publishers/google/models/gemini-3.5-flash:generateContent",
                ),
            bearerToken = "ya29.vertex-token-test".toByteArray(StandardCharsets.US_ASCII),
            headers = listOf("Content-Type" to "application/json"),
            body = "a=b&c=d".toByteArray(StandardCharsets.US_ASCII),
            timeout = Duration.ofSeconds(1),
        )

    private class RecordingFactory(
        private val channel: RecordingChannel,
    ) : PreS5VertexOneShotTlsChannelFactory {
        var openCount = 0

        override fun open(
            host: String,
            port: Int,
            timeout: Duration,
        ): PreS5VertexOneShotTlsChannel {
            openCount++
            return channel
        }
    }

    private class RecordingChannel(
        private val response: ByteArray,
        private val failWrite: Boolean = false,
    ) : PreS5VertexOneShotTlsChannel {
        var writeCount = 0
        var closed = false
        var written = ByteArray(0)
        private var offset = 0

        override fun write(bytes: ByteArray) {
            writeCount++
            written = bytes.copyOf()
            if (failWrite) {
                throw IOException("test write failure")
            }
        }

        override fun flush() = Unit

        override fun read(
            bytes: ByteArray,
            offset: Int,
            length: Int,
        ): Int {
            if (this.offset >= response.size) {
                return -1
            }
            val count = minOf(length, response.size - this.offset)
            response.copyInto(bytes, offset, this.offset, this.offset + count)
            this.offset += count
            return count
        }

        override fun close() {
            closed = true
        }
    }
}
