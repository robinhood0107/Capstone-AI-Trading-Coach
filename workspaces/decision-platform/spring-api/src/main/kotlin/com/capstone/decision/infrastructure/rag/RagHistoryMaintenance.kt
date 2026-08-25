package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagGuardHistoryPersistencePort
import io.micrometer.core.instrument.Counter
import io.micrometer.core.instrument.Gauge
import io.micrometer.core.instrument.MeterRegistry
import org.slf4j.LoggerFactory
import org.springframework.context.annotation.Configuration
import org.springframework.scheduling.annotation.EnableScheduling
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Component
import java.util.concurrent.atomic.AtomicLong

@Configuration
@EnableScheduling
class RagHistorySchedulingConfiguration

@Component
class RagHistoryPurgeJob(
    private val persistencePort: RagGuardHistoryPersistencePort,
    meterRegistry: MeterRegistry,
) {
    private val lagSeconds = AtomicLong()
    private val deletedCounter: Counter =
        Counter
            .builder("rag.history.purge.deleted")
            .description("만료된 RAG history 삭제 행 수")
            .register(meterRegistry)
    private val failureCounter: Counter =
        Counter
            .builder("rag.history.purge.failures")
            .description("RAG history purge 실패 횟수")
            .register(meterRegistry)

    init {
        Gauge
            .builder("rag.history.purge.lag.seconds", lagSeconds) { value -> value.get().toDouble() }
            .description("가장 오래된 만료 RAG history의 purge 지연")
            .register(meterRegistry)
    }

    /**
     * 한 시간마다 bounded batch만 삭제하며 owner·answer·ciphertext를 로그나 metric label에 넣지 않는다.
     */
    @Scheduled(fixedDelayString = "PT1H", initialDelayString = "PT1H")
    fun purge() {
        try {
            val result = persistencePort.purgeExpired(PURGE_BATCH_SIZE)
            deletedCounter.increment(result.deletedCount.toDouble())
            lagSeconds.set(result.oldestExpiredLagSeconds)
            if (result.oldestExpiredLagSeconds > ALERT_LAG_SECONDS) {
                logger.warn("RAG history purge lag exceeded the one-hour operational boundary.")
            }
        } catch (_: RuntimeException) {
            failureCounter.increment()
            logger.error("RAG history purge failed closed.")
        }
    }

    private companion object {
        const val PURGE_BATCH_SIZE = 500
        const val ALERT_LAG_SECONDS = 3_600L
        val logger: org.slf4j.Logger = LoggerFactory.getLogger(RagHistoryPurgeJob::class.java)
    }
}
