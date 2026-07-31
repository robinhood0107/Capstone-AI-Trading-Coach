package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagGuardHistoryPersistencePort
import com.capstone.decision.application.rag.RagPurgeResult
import io.micrometer.core.instrument.simple.SimpleMeterRegistry
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

class RagHistoryPurgeJobTest {
    @Test
    fun `purge exposes only bounded count and lag metrics`() {
        val persistence = mockk<RagGuardHistoryPersistencePort>()
        every { persistence.purgeExpired(500) } returns
            RagPurgeResult(
                deletedCount = 17,
                oldestExpiredLagSeconds = 3_601,
            )
        val registry = SimpleMeterRegistry()
        val job = RagHistoryPurgeJob(persistence, registry)

        job.purge()

        verify(exactly = 1) { persistence.purgeExpired(500) }
        assertEquals(17.0, registry.counter("rag.history.purge.deleted").count())
        assertEquals(3_601.0, registry.get("rag.history.purge.lag.seconds").gauge().value())
        assertEquals(0.0, registry.counter("rag.history.purge.failures").count())
    }

    @Test
    fun `purge failure is sanitized and the scheduler remains retryable`() {
        val persistence = mockk<RagGuardHistoryPersistencePort>()
        every { persistence.purgeExpired(500) } throws IllegalStateException("raw database detail")
        val registry = SimpleMeterRegistry()
        val job = RagHistoryPurgeJob(persistence, registry)

        job.purge()

        assertEquals(1.0, registry.counter("rag.history.purge.failures").count())
        assertEquals(0.0, registry.counter("rag.history.purge.deleted").count())
    }
}
