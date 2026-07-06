package com.capstone.decision.infrastructure.web

import jakarta.servlet.ReadListener
import jakarta.servlet.ServletInputStream
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletRequestWrapper
import java.io.BufferedReader
import java.io.ByteArrayInputStream
import java.io.InputStreamReader
import java.nio.charset.Charset
import java.nio.charset.StandardCharsets

class CachedBodyHttpServletRequest(
    request: HttpServletRequest,
) : HttpServletRequestWrapper(request) {
    val cachedBody: ByteArray = request.inputStream.readBytes()

    override fun getInputStream(): ServletInputStream = CachedBodyServletInputStream(ByteArrayInputStream(cachedBody))

    override fun getReader(): BufferedReader =
        BufferedReader(
            InputStreamReader(
                ByteArrayInputStream(cachedBody),
                characterEncoding?.let(Charset::forName) ?: StandardCharsets.UTF_8,
            ),
        )
}

private class CachedBodyServletInputStream(
    private val source: ByteArrayInputStream,
) : ServletInputStream() {
    override fun read(): Int = source.read()

    override fun isFinished(): Boolean = source.available() == 0

    override fun isReady(): Boolean = true

    override fun setReadListener(readListener: ReadListener?) {
        // Synchronous MockMvc and servlet requests do not need async callbacks here.
    }
}
