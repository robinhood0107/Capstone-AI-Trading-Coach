package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagGuardHistoryUnavailableException
import com.capstone.decision.application.rag.RagRateLimitedException
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.springframework.data.redis.core.StringRedisTemplate
import org.springframework.data.redis.core.script.RedisScript
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset

class RedisRagRateLimiterTest {
    private val redisTemplate = mockk<StringRedisTemplate>()
    private val clock = Clock.fixed(Instant.parse("2026-07-31T10:00:30Z"), ZoneOffset.UTC)
    private val properties =
        RagGuardHistoryProperties(
            rateLimitHmacKey = "r".repeat(64),
            rateLimitPerMinute = 2,
        )

    @Test
    fun `minute bucket key hides owner and permits the configured atomic count`() {
        val keys = slot<List<String>>()
        every {
            redisTemplate.execute(
                any<RedisScript<Long>>(),
                capture(keys),
                "61000",
            )
        } returnsMany listOf(1L, 2L)
        val limiter = RedisRagRateLimiter(redisTemplate, properties, clock)

        limiter.acquire("usr_demo_user")
        limiter.acquire("usr_demo_user")

        assertTrue(keys.captured.single().matches(Regex("^rag-rate:v1:[0-9a-f]{64}$")))
        assertFalse(keys.captured.single().contains("usr_demo_user"))
        verify(exactly = 2) {
            redisTemplate.execute(
                any<RedisScript<Long>>(),
                any<List<String>>(),
                "61000",
            )
        }
    }

    @Test
    fun `count above the configured bound is rejected without another operation`() {
        every {
            redisTemplate.execute(
                any<RedisScript<Long>>(),
                any<List<String>>(),
                "61000",
            )
        } returns 3L
        val limiter = RedisRagRateLimiter(redisTemplate, properties, clock)

        assertThrows<RagRateLimitedException> {
            limiter.acquire("usr_demo_user")
        }
        verify(exactly = 1) {
            redisTemplate.execute(
                any<RedisScript<Long>>(),
                any<List<String>>(),
                "61000",
            )
        }
    }

    @Test
    fun `Redis error and missing result both fail closed`() {
        every {
            redisTemplate.execute(
                any<RedisScript<Long>>(),
                any<List<String>>(),
                "61000",
            )
        } throws IllegalStateException("synthetic Redis failure")
        val failedLimiter = RedisRagRateLimiter(redisTemplate, properties, clock)

        assertThrows<RagGuardHistoryUnavailableException> {
            failedLimiter.acquire("usr_demo_user")
        }

        every {
            redisTemplate.execute(
                any<RedisScript<Long>>(),
                any<List<String>>(),
                "61000",
            )
        } returns null
        val missingLimiter = RedisRagRateLimiter(redisTemplate, properties, clock)

        assertThrows<RagGuardHistoryUnavailableException> {
            missingLimiter.acquire("usr_demo_user")
        }
    }
}
