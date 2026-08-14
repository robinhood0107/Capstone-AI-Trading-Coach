package com.capstone.decision.infrastructure.vertex

import java.net.InetSocketAddress
import java.net.Proxy
import java.net.Socket
import java.net.URI
import java.nio.charset.StandardCharsets
import java.time.Duration
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory

internal data class PreS5VertexOneShotHttpsRequest(
    val endpoint: URI,
    val bearerToken: ByteArray? = null,
    val headers: List<Pair<String, String>>,
    val body: ByteArray,
    val timeout: Duration,
)

internal data class PreS5VertexOneShotHttpsResponse(
    val statusCode: Int,
    val body: ByteArray,
)

/**
 * TLS channel factory는 socket 생성 횟수를 transport의 한 request에 정확히 하나로 제한하는 seam이다.
 * default 구현은 proxy selector나 high-level HTTP client를 사용하지 않아 connect failure를 재전송하지 않는다.
 */
internal interface PreS5VertexOneShotTlsChannelFactory {
    fun open(
        host: String,
        port: Int,
        timeout: Duration,
    ): PreS5VertexOneShotTlsChannel
}

internal interface PreS5VertexOneShotTlsChannel : AutoCloseable {
    fun write(bytes: ByteArray)

    fun flush()

    fun read(
        bytes: ByteArray,
        offset: Int,
        length: Int,
    ): Int
}

internal class PreS5VertexDirectTlsChannelFactory : PreS5VertexOneShotTlsChannelFactory {
    override fun open(
        host: String,
        port: Int,
        timeout: Duration,
    ): PreS5VertexOneShotTlsChannel {
        val timeoutMillis = timeout.toMillis().coerceIn(1_000, 30_000).toInt()
        val tcp = Socket(Proxy.NO_PROXY)
        try {
            tcp.connect(InetSocketAddress(host, port), timeoutMillis)
            tcp.soTimeout = timeoutMillis
            val sslFactory = SSLSocketFactory.getDefault() as SSLSocketFactory
            val ssl =
                (sslFactory.createSocket(tcp, host, port, true) as SSLSocket).apply {
                    sslParameters = sslParameters.also { parameters -> parameters.endpointIdentificationAlgorithm = "HTTPS" }
                    soTimeout = timeoutMillis
                    startHandshake()
                }
            return PreS5VertexSslSocketChannel(ssl)
        } catch (error: Exception) {
            runCatching { tcp.close() }
            throw error
        }
    }
}

private class PreS5VertexSslSocketChannel(
    private val socket: SSLSocket,
) : PreS5VertexOneShotTlsChannel {
    private val output = socket.outputStream
    private val input = socket.inputStream

    override fun write(bytes: ByteArray) {
        output.write(bytes)
    }

    override fun flush() {
        output.flush()
    }

    override fun read(
        bytes: ByteArray,
        offset: Int,
        length: Int,
    ): Int = input.read(bytes, offset, length)

    override fun close() {
        socket.close()
    }
}

/**
 * POST body를 direct TLS socket에 한 번만 기록하는 minimal HTTP/1.1 transport다. redirect, proxy,
 * connection replay와 HTTP/2 stream replay를 제공하지 않으며 malformed/oversized response는 fail-closed한다.
 */
internal class PreS5VertexOneShotHttpsTransport(
    private val channelFactory: PreS5VertexOneShotTlsChannelFactory = PreS5VertexDirectTlsChannelFactory(),
) {
    fun execute(
        request: PreS5VertexOneShotHttpsRequest,
        maximumResponseBytes: Int,
    ): PreS5VertexOneShotHttpsResponse {
        var headerBytes: ByteArray? = null
        var wireBytes: ByteArray? = null
        try {
            require(request.endpoint.scheme == "https")
            require(request.endpoint.port == -1)
            require(request.endpoint.userInfo == null)
            require(request.endpoint.rawQuery == null && request.endpoint.rawFragment == null)
            require(
                request.bearerToken == null ||
                    (
                        request.bearerToken.size in MINIMUM_BEARER_TOKEN_BYTES..MAXIMUM_BEARER_TOKEN_BYTES &&
                            request.bearerToken.all { byte -> byte.toInt() in 0x21..0x7e }
                    ),
            )
            require(request.body.size <= MAX_REQUEST_BYTES)
            require(maximumResponseBytes in 0..MAX_RESPONSE_BYTES)
            require(request.timeout in MIN_TIMEOUT..MAX_TIMEOUT)
            require(request.headers.size <= MAX_REQUEST_HEADERS)
            require(
                request.headers.all { (name, value) ->
                    HEADER_NAME.matches(name) && value.isNotBlank() && value.all { it.code in 0x20..0x7e }
                },
            )
            val requestHeaderNames = request.headers.map { it.first.lowercase() }
            require(requestHeaderNames.distinct().size == request.headers.size)
            require(requestHeaderNames.none { it in RESERVED_REQUEST_HEADERS })

            val path = request.endpoint.rawPath?.takeIf { it.isNotEmpty() } ?: "/"
            val targetPrefix = "POST $path HTTP/1.1\r\nHost: ".toByteArray(StandardCharsets.US_ASCII)
            val remainingHeader =
                buildString {
                    append(request.endpoint.host)
                    append("\r\nConnection: close\r\nContent-Length: ")
                    append(request.body.size)
                    append("\r\n")
                    if (request.bearerToken != null) {
                        append("Authorization: Bearer ")
                    }
                }.toByteArray(StandardCharsets.US_ASCII)
            val trailingHeader =
                buildString {
                    if (request.bearerToken != null) {
                        append("\r\n")
                    }
                    request.headers.forEach { (name, value) ->
                        append(name)
                        append(": ")
                        append(value)
                        append("\r\n")
                    }
                    append("\r\n")
                }.toByteArray(StandardCharsets.US_ASCII)
            val bearerTokenLength = request.bearerToken?.size ?: 0
            val header =
                ByteArray(
                    targetPrefix.size + remainingHeader.size + bearerTokenLength + trailingHeader.size,
                )
            var headerOffset = 0
            targetPrefix.copyInto(header, destinationOffset = headerOffset)
            headerOffset += targetPrefix.size
            remainingHeader.copyInto(header, destinationOffset = headerOffset)
            headerOffset += remainingHeader.size
            request.bearerToken?.copyInto(header, destinationOffset = headerOffset)
            headerOffset += bearerTokenLength
            trailingHeader.copyInto(header, destinationOffset = headerOffset)
            targetPrefix.fill(0)
            remainingHeader.fill(0)
            trailingHeader.fill(0)
            headerBytes = header
            require(header.size <= MAX_HEADER_BYTES)
            val wire = ByteArray(header.size + request.body.size)
            wireBytes = wire
            header.copyInto(wire)
            request.body.copyInto(wire, destinationOffset = header.size)

            channelFactory.open(request.endpoint.host, HTTPS_PORT, request.timeout).use { channel ->
                // outputStream.write 호출은 정확히 한 번이다. IOException 뒤 새 channel이나 body replay를 만들지 않는다.
                channel.write(wire)
                channel.flush()
                return readResponse(channel, maximumResponseBytes)
            }
        } catch (error: PreS5VertexOneShotHttpsTransportException) {
            throw error
        } catch (_: Exception) {
            throw PreS5VertexOneShotHttpsTransportException()
        } finally {
            headerBytes?.fill(0)
            wireBytes?.fill(0)
            request.bearerToken?.fill(0)
        }
    }

    private fun readResponse(
        channel: PreS5VertexOneShotTlsChannel,
        maximumResponseBytes: Int,
    ): PreS5VertexOneShotHttpsResponse {
        val statusLine = readLine(channel, MAX_STATUS_LINE_BYTES)
        val status =
            STATUS_LINE
                .matchEntire(statusLine)
                ?.groupValues
                ?.get(1)
                ?.toIntOrNull()
                ?: throw PreS5VertexOneShotHttpsTransportException()
        require(status in 100..599)
        val headers = readHeaders(channel)
        val contentLength = headers["content-length"]?.toIntOrNull()
        val transferEncoding = headers["transfer-encoding"]
        require(!(contentLength != null && transferEncoding != null))
        val body =
            when {
                contentLength != null -> {
                    require(contentLength in 0..maximumResponseBytes)
                    readExactly(channel, contentLength)
                }
                transferEncoding == "chunked" -> readChunked(channel, maximumResponseBytes)
                else -> throw PreS5VertexOneShotHttpsTransportException()
            }
        return PreS5VertexOneShotHttpsResponse(status, body)
    }

    private fun readHeaders(channel: PreS5VertexOneShotTlsChannel): Map<String, String> {
        val headers = linkedMapOf<String, String>()
        var consumedBytes = 0
        repeat(MAX_RESPONSE_HEADERS) {
            val line = readLine(channel, MAX_HEADER_LINE_BYTES)
            consumedBytes += line.length + 2
            require(consumedBytes <= MAX_HEADER_BYTES)
            if (line.isEmpty()) {
                return headers
            }
            val separator = line.indexOf(':')
            require(separator in 1 until line.lastIndex)
            val name = line.substring(0, separator).lowercase()
            val value = line.substring(separator + 1).trim()
            require(HEADER_NAME.matches(name) && value.isNotEmpty() && value.all { it.code in 0x20..0x7e })
            // Content framing의 중복은 request smuggling 경계라 계속 거부한다. Vary처럼 반복 가능한
            // bounded metadata는 사용하지 않으므로 첫 값만 보존해 정상 Google 응답을 폐기하지 않는다.
            if (name in headers) {
                require(name !in SINGLETON_RESPONSE_HEADERS)
            } else {
                headers[name] = value
            }
        }
        throw PreS5VertexOneShotHttpsTransportException()
    }

    private fun readChunked(
        channel: PreS5VertexOneShotTlsChannel,
        maximumResponseBytes: Int,
    ): ByteArray {
        val chunks = ArrayList<ByteArray>()
        var total = 0
        try {
            while (true) {
                val sizeLine = readLine(channel, MAX_CHUNK_LINE_BYTES)
                require(CHUNK_SIZE.matches(sizeLine))
                val chunkSize = sizeLine.toInt(16)
                require(chunkSize in 0..maximumResponseBytes - total)
                if (chunkSize == 0) {
                    require(readLine(channel, MAX_HEADER_LINE_BYTES).isEmpty())
                    val body = ByteArray(total)
                    var offset = 0
                    chunks.forEach { chunk ->
                        chunk.copyInto(body, destinationOffset = offset)
                        offset += chunk.size
                    }
                    return body
                }
                val chunk = readExactly(channel, chunkSize)
                chunks += chunk
                total += chunk.size
                require(readLine(channel, 2).isEmpty())
            }
        } finally {
            chunks.forEach { it.fill(0) }
        }
    }

    private fun readExactly(
        channel: PreS5VertexOneShotTlsChannel,
        length: Int,
    ): ByteArray {
        val result = ByteArray(length)
        var offset = 0
        while (offset < result.size) {
            val read = channel.read(result, offset, result.size - offset)
            require(read > 0)
            offset += read
        }
        return result
    }

    private fun readLine(
        channel: PreS5VertexOneShotTlsChannel,
        maximumBytes: Int,
    ): String {
        val bytes = ByteArray(maximumBytes)
        val singleByte = ByteArray(1)
        var offset = 0
        try {
            while (offset < bytes.size) {
                require(channel.read(singleByte, 0, 1) == 1)
                when (singleByte[0].toInt().toChar()) {
                    '\r' -> {
                        require(channel.read(singleByte, 0, 1) == 1 && singleByte[0].toInt().toChar() == '\n')
                        return bytes.copyOf(offset).toString(StandardCharsets.US_ASCII)
                    }
                    '\n' -> throw PreS5VertexOneShotHttpsTransportException()
                    else -> bytes[offset++] = singleByte[0]
                }
            }
            throw PreS5VertexOneShotHttpsTransportException()
        } finally {
            singleByte.fill(0)
            bytes.fill(0)
        }
    }

    private companion object {
        const val HTTPS_PORT = 443
        const val MAX_REQUEST_BYTES = 60_000
        const val MAX_RESPONSE_BYTES = 65_536
        const val MAX_REQUEST_HEADERS = 8
        const val MAX_RESPONSE_HEADERS = 32
        const val MAX_HEADER_BYTES = 16_384
        const val MAX_STATUS_LINE_BYTES = 256
        const val MAX_HEADER_LINE_BYTES = 4_096
        const val MAX_CHUNK_LINE_BYTES = 16
        val MIN_TIMEOUT: Duration = Duration.ofSeconds(1)
        val MAX_TIMEOUT: Duration = Duration.ofSeconds(30)
        val HEADER_NAME = Regex("^[A-Za-z0-9-]{1,64}$")
        val STATUS_LINE = Regex("^HTTP/1\\.1 ([1-5][0-9]{2})(?: .*)?$")
        val CHUNK_SIZE = Regex("^[0-9A-Fa-f]{1,8}$")
        val RESERVED_REQUEST_HEADERS = setOf("host", "connection", "content-length", "transfer-encoding", "authorization")
        val SINGLETON_RESPONSE_HEADERS = setOf("content-length", "transfer-encoding")
        const val MINIMUM_BEARER_TOKEN_BYTES = 16
        const val MAXIMUM_BEARER_TOKEN_BYTES = 8 * 1024
    }
}

internal class PreS5VertexOneShotHttpsTransportException : RuntimeException()
