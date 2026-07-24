package com.capstone.decision

import com.capstone.decision.domain.risk.EvaluationBounds
import com.capstone.decision.infrastructure.risk.BoundedEvaluationSourceCallCoordinator
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class BoundedEvaluationSourceCallCoordinatorTest {
    @Test
    fun `shared 900ms budget stops later source calls after two bounded timeouts`() {
        val coordinator = BoundedEvaluationSourceCallCoordinator()
        val physicalCalls = AtomicInteger()
        val startedAt = System.nanoTime()
        val deadline = startedAt + EvaluationBounds.EVALUATION_DEADLINE.toNanos()
        try {
            repeat(2) {
                assertEquals(
                    "UNAVAILABLE",
                    coordinator.call(deadline, "UNAVAILABLE") {
                        physicalCalls.incrementAndGet()
                        Thread.sleep(2_000)
                        "AVAILABLE"
                    },
                )
            }
            assertEquals(
                "UNAVAILABLE",
                coordinator.call(deadline, "UNAVAILABLE") {
                    physicalCalls.incrementAndGet()
                    "AVAILABLE"
                },
            )
        } finally {
            coordinator.close()
        }

        val elapsedMillis = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAt)
        assertEquals(2, physicalCalls.get())
        assertTrue(elapsedMillis in 800..1_200, "unexpected bounded elapsed time: $elapsedMillis ms")
    }

    @Test
    fun `ninth concurrent request returns unavailable before a ninth physical source call`() {
        val coordinator = BoundedEvaluationSourceCallCoordinator()
        val callers = Executors.newFixedThreadPool(9)
        val started = CountDownLatch(EvaluationBounds.MAX_CONCURRENCY)
        val release = CountDownLatch(1)
        val physicalCalls = AtomicInteger()
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(2)
        try {
            val firstEight =
                (0 until EvaluationBounds.MAX_CONCURRENCY).map {
                    callers.submit<String> {
                        coordinator.call(deadline, "UNAVAILABLE") {
                            physicalCalls.incrementAndGet()
                            started.countDown()
                            release.await(2, TimeUnit.SECONDS)
                            "AVAILABLE"
                        }
                    }
                }
            assertTrue(started.await(1, TimeUnit.SECONDS))

            val ninth =
                callers.submit<String> {
                    coordinator.call(deadline, "UNAVAILABLE") {
                        physicalCalls.incrementAndGet()
                        "AVAILABLE"
                    }
                }
            assertEquals("UNAVAILABLE", ninth.get(250, TimeUnit.MILLISECONDS))
            assertEquals(EvaluationBounds.MAX_CONCURRENCY, physicalCalls.get())

            release.countDown()
            firstEight.forEach { it.get(1, TimeUnit.SECONDS) }
        } finally {
            release.countDown()
            callers.shutdownNow()
            coordinator.close()
        }
    }
}
