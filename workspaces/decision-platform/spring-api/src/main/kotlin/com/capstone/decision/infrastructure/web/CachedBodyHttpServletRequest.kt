package com.capstone.decision.infrastructure.web

import jakarta.servlet.ReadListener
import jakarta.servlet.ServletInputStream
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletRequestWrapper
import java.io.BufferedReader
import java.io.ByteArrayInputStream
import java.io.InputStreamReader
import java.nio.charset.Charset
import java.nio.charset.IllegalCharsetNameException
import java.nio.charset.StandardCharsets
import java.nio.charset.UnsupportedCharsetException

// idempotency hash 계산 후에도 controller가 같은 request body를 읽을 수 있어야 한다.
class CachedBodyHttpServletRequest(
    request: HttpServletRequest,
    maxBodyBytes: Int,
) : HttpServletRequestWrapper(request) {
    val cachedBody: ByteArray

    init {
        if (request.contentLengthLong > maxBodyBytes) {
            throw RequestBodyTooLargeException()
        }
        val bounded = request.inputStream.readNBytes(maxBodyBytes + 1)
        if (bounded.size > maxBodyBytes) {
            throw RequestBodyTooLargeException()
        }
        cachedBody = bounded
    }

    override fun getInputStream(): ServletInputStream = CachedBodyServletInputStream(ByteArrayInputStream(cachedBody))

    override fun getReader(): BufferedReader =
        BufferedReader(
            InputStreamReader(
                ByteArrayInputStream(cachedBody),
                safeCharacterEncoding(),
            ),
        )

    private fun safeCharacterEncoding(): Charset =
        try {
            characterEncoding?.let(Charset::forName) ?: StandardCharsets.UTF_8
        } catch (_: IllegalCharsetNameException) {
            StandardCharsets.UTF_8
        } catch (_: UnsupportedCharsetException) {
            StandardCharsets.UTF_8
        }
}

class RequestBodyTooLargeException : RuntimeException("request body exceeded configured safety limit")

private class CachedBodyServletInputStream(
    private val source: ByteArrayInputStream,
) : ServletInputStream() {
    override fun read(): Int = source.read()

    override fun isFinished(): Boolean = source.available() == 0

    override fun isReady(): Boolean = true

    override fun setReadListener(readListener: ReadListener?) {
        // 현재 S0.3 동기 servlet/MockMvc 흐름에서는 async read callback이 필요 없다.
    }
}
