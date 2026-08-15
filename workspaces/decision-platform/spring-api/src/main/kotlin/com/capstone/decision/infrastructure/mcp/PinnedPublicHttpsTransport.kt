package com.capstone.decision.infrastructure.mcp

import java.io.BufferedInputStream
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
    ): PublicHttpsResponse {
        require(uri.scheme == "https" && uri.port in setOf(-1, 443))
        require(maximumBodyBytes in 1..2_000_000)
        val path = (uri.rawPath?.ifBlank { "/" } ?: "/") + (uri.rawQuery?.let { "?$it" } ?: "")
        require(path.all { it.code in 0x21..0x7e } && '\r' !in path && '\n' !in path)
        val plain = Socket()
        plain.connect(InetSocketAddress(pinnedAddress, 443), timeout.toMillis().toInt())
        plain.soTimeout = timeout.toMillis().toInt()
        val socket = socketFactory.createSocket(plain, uri.host, 443, true) as SSLSocket
        socket.use { tls ->
            tls.sslParameters =
                tls.sslParameters.apply {
                    endpointIdentificationAlgorithm = "HTTPS"
                    serverNames = listOf(SNIHostName(uri.host))
                }
            tls.startHandshake()
            val request =
                buildString {
                    append("GET ").append(path).append(" HTTP/1.1\r\n")
                    append("Host: ").append(uri.host).append("\r\n")
                    append("Accept: text/html,text/plain,application/pdf\r\n")
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
            return parseResponse(BufferedInputStream(tls.inputStream), maximumBodyBytes)
        }
    }

    internal fun parseResponse(
        input: BufferedInputStream,
        maximumBodyBytes: Int,
    ): PublicHttpsResponse {
        val statusLine = readLine(input, 256)
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
            val line = readLine(input, MAX_HEADER_LINE_BYTES)
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
                            readExactly(input, length)
                        }
                        transfer == "chunked" -> readChunked(input, maximumBodyBytes)
                        transfer == null -> readUntilEof(input, maximumBodyBytes)
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
    ): ByteArray {
        val bytes = ArrayList<Byte>()
        while (true) {
            val sizeText = readLine(input, 64).substringBefore(';')
            val size = sizeText.toIntOrNull(16) ?: throw IllegalArgumentException("Invalid chunk")
            require(size >= 0 && bytes.size + size <= maximum)
            if (size == 0) {
                while (readLine(input, 8_192).isNotEmpty()) {
                    require(bytes.size <= maximum)
                }
                return bytes.toByteArray()
            }
            readExactly(input, size).forEach(bytes::add)
            require(readLine(input, 2).isEmpty())
        }
    }

    private fun readUntilEof(
        input: BufferedInputStream,
        maximum: Int,
    ): ByteArray {
        val bytes = ArrayList<Byte>()
        val buffer = ByteArray(8_192)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            require(bytes.size + count <= maximum)
            repeat(count) { index -> bytes.add(buffer[index]) }
        }
        buffer.fill(0)
        return bytes.toByteArray()
    }

    private fun readExactly(
        input: BufferedInputStream,
        count: Int,
    ): ByteArray {
        val output = ByteArray(count)
        var offset = 0
        while (offset < count) {
            val read = input.read(output, offset, count - offset)
            require(read > 0)
            offset += read
        }
        return output
    }

    private fun readLine(
        input: BufferedInputStream,
        maximum: Int,
    ): String {
        val bytes = ArrayList<Byte>()
        while (bytes.size <= maximum) {
            val value = input.read()
            require(value >= 0)
            if (value == '\r'.code) {
                require(input.read() == '\n'.code)
                return bytes.toByteArray().toString(StandardCharsets.US_ASCII)
            }
            require(value in 0x20..0x7e)
            bytes.add(value.toByte())
        }
        throw IllegalArgumentException("HTTP line exceeds cap")
    }

    private companion object {
        val STATUS = Regex("^HTTP/1\\.[01] ([1-5][0-9]{2})(?: .*)?$")
        val HEADER_NAME = Regex("^[a-z0-9!#$%&'*+.^_`|~-]{1,128}$")
        const val MAX_HEADER_LINE_BYTES = 16_384
    }
}
