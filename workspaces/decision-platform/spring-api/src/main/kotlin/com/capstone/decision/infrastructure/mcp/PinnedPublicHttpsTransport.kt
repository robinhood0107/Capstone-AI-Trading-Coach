package com.capstone.decision.infrastructure.mcp

import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URI
import java.nio.charset.StandardCharsets
import java.time.Duration
import javax.net.ssl.SNIHostName
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory

data class PublicHttpsResponse(
    val statusCode: Int,
    val headers: Map<String, List<String>>,
    val body: ByteArray,
)

fun interface PublicHttpsTransport {
    fun get(
        uri: URI,
        pinnedAddress: InetAddress,
        maximumBodyBytes: Int,
        deadlineNanos: Long,
    ): PublicHttpsResponse
}

/** 검증한 public IP에만 직접 연결하고 TLS hostname 검증은 원래 URI host로 수행하는 no-proxy one-shot GET이다. */
class PinnedPublicHttpsTransport(
    private val socketFactory: SSLSocketFactory = SSLSocketFactory.getDefault() as SSLSocketFactory,
    private val timeout: Duration = Duration.ofSeconds(10),
) : PublicHttpsTransport {
    override fun get(
        uri: URI,
        pinnedAddress: InetAddress,
        maximumBodyBytes: Int,
        deadlineNanos: Long,
    ): PublicHttpsResponse {
        require(uri.scheme == "https" && uri.port in setOf(-1, 443))
        require(maximumBodyBytes in 1..2_000_000)
        val path = (uri.rawPath?.ifBlank { "/" } ?: "/") + (uri.rawQuery?.let { "?$it" } ?: "")
        require(path.all { it.code in 0x21..0x7e } && '\r' !in path && '\n' !in path)
        val plain = Socket()
        plain.connect(InetSocketAddress(pinnedAddress, 443), remainingTimeoutMillis(deadlineNanos))
        plain.soTimeout = remainingTimeoutMillis(deadlineNanos)
        val socket = socketFactory.createSocket(plain, uri.host, 443, true) as SSLSocket
        socket.use { tls ->
            tls.sslParameters =
                tls.sslParameters.apply {
                    endpointIdentificationAlgorithm = "HTTPS"
                    serverNames = listOf(SNIHostName(uri.host))
                }
            tls.soTimeout = remainingTimeoutMillis(deadlineNanos)
            tls.startHandshake()
            val request =
                buildString {
                    append("GET ").append(path).append(" HTTP/1.1\r\n")
                    append("Host: ").append(uri.host).append("\r\n")
                    append("Accept: text/html,text/plain\r\n")
                    append("Accept-Encoding: identity\r\n")
                    append("User-Agent: Capstone-S4.9-EvidenceReader/1.0\r\n")
                    append("Connection: close\r\n\r\n")
                }.toByteArray(StandardCharsets.US_ASCII)
            try {
                tls.outputStream.write(request)
                tls.outputStream.flush()
            } finally {
                request.fill(0)
            }
            tls.soTimeout = remainingTimeoutMillis(deadlineNanos)
            return parseResponse(BufferedInputStream(tls.inputStream), maximumBodyBytes, deadlineNanos)
        }
    }

    internal fun parseResponse(
        input: BufferedInputStream,
        maximumBodyBytes: Int,
        deadlineNanos: Long = Long.MAX_VALUE,
    ): PublicHttpsResponse {
        val statusLine = readLine(input, 256, deadlineNanos)
        val status =
            STATUS
                .matchEntire(statusLine)
                ?.groupValues
                ?.get(1)
                ?.toIntOrNull()
        require(status != null && status in 100..599)
        val headers = linkedMapOf<String, MutableList<String>>()
        var headerBytes = statusLine.length + 2
        repeat(64) {
            // CSP 같은 안전한 비핵심 헤더는 길 수 있으므로 전체 32 KiB 상한 안에서 한 줄 16 KiB까지 허용한다.
            val line = readLine(input, MAX_HEADER_LINE_BYTES, deadlineNanos)
            headerBytes += line.length + 2
            require(headerBytes <= 32_768)
            if (line.isEmpty()) {
                val encoding = headers["content-encoding"]?.singleOrNull()?.lowercase()
                require(encoding == null || encoding == "identity")
                val length = headers["content-length"]?.singleOrNull()?.toIntOrNull()
                val transfer = headers["transfer-encoding"]?.singleOrNull()?.lowercase()
                require(!(length != null && transfer != null))
                val body =
                    when {
                        length != null -> {
                            require(length in 0..maximumBodyBytes)
                            readExactly(input, length, deadlineNanos)
                        }
                        transfer == "chunked" -> readChunked(input, maximumBodyBytes, deadlineNanos)
                        transfer == null -> readUntilEof(input, maximumBodyBytes, deadlineNanos)
                        else -> throw IllegalArgumentException("Unsupported transfer encoding")
                    }
                return PublicHttpsResponse(status, headers.mapValues { entry -> entry.value.toList() }, body)
            }
            require(!line.startsWith(' ') && !line.startsWith('\t'))
            val separator = line.indexOf(':')
            require(separator >= 1)
            val name = line.substring(0, separator).lowercase()
            val value = line.substring(separator + 1).trim()
            // RFC field-value는 빈 값도 허용한다. 필요한 framing/MIME 헤더는 소비 지점에서 별도로 엄격 검증한다.
            require(HEADER_NAME.matches(name) && value.all { it.code in 0x20..0x7e })
            headers.getOrPut(name) { mutableListOf() }.add(value)
        }
        throw IllegalArgumentException("Too many headers")
    }

    private fun readChunked(
        input: BufferedInputStream,
        maximum: Int,
        deadlineNanos: Long,
    ): ByteArray {
        val output = ByteArrayOutputStream(maximum)
        var chunkCount = 0
        while (true) {
            require(++chunkCount <= MAX_CHUNKS)
            val sizeText = readLine(input, 64, deadlineNanos).substringBefore(';')
            val parsed = sizeText.toLongOrNull(16) ?: throw IllegalArgumentException("Invalid chunk")
            require(parsed in 0..Int.MAX_VALUE.toLong())
            val size = parsed.toInt()
            require(output.size() <= maximum && size <= maximum - output.size())
            if (size == 0) {
                var trailerCount = 0
                var trailerBytes = 0
                while (true) {
                    val trailer = readLine(input, MAX_TRAILER_LINE_BYTES, deadlineNanos)
                    if (trailer.isEmpty()) return output.toByteArray()
                    require(++trailerCount <= MAX_TRAILERS)
                    trailerBytes = Math.addExact(trailerBytes, trailer.length + 2)
                    require(trailerBytes <= MAX_TRAILER_BYTES)
                }
            }
            output.write(readExactly(input, size, deadlineNanos))
            require(readLine(input, 2, deadlineNanos).isEmpty())
        }
    }

    private fun readUntilEof(
        input: BufferedInputStream,
        maximum: Int,
        deadlineNanos: Long,
    ): ByteArray {
        val output = ByteArrayOutputStream(maximum)
        val buffer = ByteArray(8_192)
        while (true) {
            requireDeadline(deadlineNanos)
            val count = input.read(buffer)
            if (count < 0) break
            require(output.size() <= maximum && count <= maximum - output.size())
            output.write(buffer, 0, count)
        }
        buffer.fill(0)
        return output.toByteArray()
    }

    private fun readExactly(
        input: BufferedInputStream,
        count: Int,
        deadlineNanos: Long,
    ): ByteArray {
        val output = ByteArray(count)
        var offset = 0
        while (offset < count) {
            requireDeadline(deadlineNanos)
            val read = input.read(output, offset, count - offset)
            require(read > 0)
            offset += read
        }
        return output
    }

    private fun readLine(
        input: BufferedInputStream,
        maximum: Int,
        deadlineNanos: Long,
    ): String {
        val bytes = ArrayList<Byte>()
        while (bytes.size <= maximum) {
            requireDeadline(deadlineNanos)
            val value = input.read()
            require(value >= 0)
            if (value == '\r'.code) {
                requireDeadline(deadlineNanos)
                require(input.read() == '\n'.code)
                return bytes.toByteArray().toString(StandardCharsets.US_ASCII)
            }
            require(value in 0x20..0x7e)
            bytes.add(value.toByte())
        }
        throw IllegalArgumentException("HTTP line exceeds cap")
    }

    private fun remainingTimeoutMillis(deadlineNanos: Long): Int {
        val remainingNanos = deadlineNanos - System.nanoTime()
        require(remainingNanos > 0)
        val remainingMillis = Math.max(1L, Duration.ofNanos(remainingNanos).toMillis())
        return minOf(timeout.toMillis(), remainingMillis, Int.MAX_VALUE.toLong()).toInt()
    }

    private fun requireDeadline(deadlineNanos: Long) {
        require(System.nanoTime() < deadlineNanos) { "Public HTTPS deadline exceeded" }
    }

    private companion object {
        val STATUS = Regex("^HTTP/1\\.[01] ([1-5][0-9]{2})(?: .*)?$")
        val HEADER_NAME = Regex("^[a-z0-9!#$%&'*+.^_`|~-]{1,128}$")
        const val MAX_HEADER_LINE_BYTES = 16_384
        const val MAX_CHUNKS = 4_096
        const val MAX_TRAILERS = 32
        const val MAX_TRAILER_LINE_BYTES = 8_192
        const val MAX_TRAILER_BYTES = 32_768
    }
}
