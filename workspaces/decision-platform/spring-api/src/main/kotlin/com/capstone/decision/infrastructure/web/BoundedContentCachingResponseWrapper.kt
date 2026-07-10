package com.capstone.decision.infrastructure.web

import jakarta.servlet.ServletOutputStream
import jakarta.servlet.WriteListener
import jakarta.servlet.http.HttpServletResponse
import jakarta.servlet.http.HttpServletResponseWrapper
import java.io.ByteArrayOutputStream
import java.io.OutputStreamWriter
import java.io.PrintWriter
import java.nio.charset.Charset

// 멱등 replay 응답을 downstream에 전달하기 전에 보관하되, 큰 응답이 heap을 무제한 점유하지 못하게 한다.
class BoundedContentCachingResponseWrapper(
    response: HttpServletResponse,
    private val maxBytes: Int,
) : HttpServletResponseWrapper(response) {
    private val buffer = ByteArrayOutputStream(minOf(maxBytes, DEFAULT_INITIAL_CAPACITY))
    private var outputStreamRequested = false
    private var writerRequested = false
    private var cachedWriter: PrintWriter? = null

    var overflowed: Boolean = false
        private set

    private val cachingOutputStream =
        object : ServletOutputStream() {
            override fun write(value: Int) {
                if (buffer.size() < maxBytes) {
                    buffer.write(value)
                } else {
                    overflowed = true
                }
            }

            override fun write(
                bytes: ByteArray,
                offset: Int,
                length: Int,
            ) {
                val remaining = maxBytes - buffer.size()
                if (length > remaining) {
                    overflowed = true
                }
                val accepted = minOf(length, remaining.coerceAtLeast(0))
                if (accepted > 0) {
                    buffer.write(bytes, offset, accepted)
                }
            }

            override fun isReady(): Boolean = true

            override fun setWriteListener(listener: WriteListener?) {
                require(listener == null) { "non-blocking response writes are not supported" }
            }
        }

    override fun getOutputStream(): ServletOutputStream {
        check(!writerRequested) { "getWriter() has already been called" }
        outputStreamRequested = true
        return cachingOutputStream
    }

    override fun getWriter(): PrintWriter {
        check(!outputStreamRequested) { "getOutputStream() has already been called" }
        writerRequested = true
        return cachedWriter ?: PrintWriter(OutputStreamWriter(cachingOutputStream, responseCharset())).also {
            cachedWriter = it
        }
    }

    override fun flushBuffer() {
        cachedWriter?.flush()
        cachingOutputStream.flush()
    }

    override fun resetBuffer() {
        super.resetBuffer()
        clearBuffer()
    }

    override fun reset() {
        super.reset()
        clearBuffer()
        outputStreamRequested = false
        writerRequested = false
        cachedWriter = null
    }

    val contentAsByteArray: ByteArray
        get() {
            flushBuffer()
            return buffer.toByteArray()
        }

    fun copyBodyToResponse() {
        val content = contentAsByteArray
        response.outputStream.write(content)
        response.flushBuffer()
    }

    private fun clearBuffer() {
        buffer.reset()
        overflowed = false
    }

    private fun responseCharset(): Charset = Charset.forName(characterEncoding)

    companion object {
        private const val DEFAULT_INITIAL_CAPACITY = 8 * 1024
    }
}
