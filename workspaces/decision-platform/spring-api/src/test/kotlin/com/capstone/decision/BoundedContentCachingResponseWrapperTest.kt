package com.capstone.decision

import com.capstone.decision.infrastructure.web.BoundedContentCachingResponseWrapper
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.springframework.mock.web.MockHttpServletResponse

class BoundedContentCachingResponseWrapperTest {
    @Test
    fun `oversized response never buffers beyond configured bytes`() {
        val wrapper = BoundedContentCachingResponseWrapper(MockHttpServletResponse(), 256)

        wrapper.outputStream.write(ByteArray(1_048_576) { 'x'.code.toByte() })

        assertTrue(wrapper.overflowed)
        assertEquals(256, wrapper.contentAsByteArray.size)
    }
}
