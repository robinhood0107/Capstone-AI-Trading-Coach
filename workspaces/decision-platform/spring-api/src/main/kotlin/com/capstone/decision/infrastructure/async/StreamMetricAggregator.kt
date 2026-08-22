package com.capstone.decision.infrastructure.async

import net.javacrumbs.shedlock.spring.annotation.SchedulerLock
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.ObjectProvider
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Component

/** DB-only observability aggregation over persisted Decision Platform records. */
@Component
@ConditionalOnProperty(name = ["app.async.polling-enabled"], havingValue = "true")
class StreamMetricAggregator(
    private val jdbcProvider: ObjectProvider<JdbcTemplate>,
) {
    @Scheduled(cron = "0 * * * * *", scheduler = "streamMetricTaskScheduler")
    @SchedulerLock(name = "s7.stream-metric.decision", lockAtMostFor = "PT30S")
    fun aggregateDecisionDistribution() = aggregate("aggregate_decision_distribution")

    @Scheduled(cron = "5 * * * * *", scheduler = "streamMetricTaskScheduler")
    @SchedulerLock(name = "s7.stream-metric.failed-jobs", lockAtMostFor = "PT30S")
    fun aggregateFailedJobs() = aggregate("aggregate_failed_jobs")

    @Scheduled(cron = "10 */5 * * * *", scheduler = "streamMetricTaskScheduler")
    @SchedulerLock(name = "s7.stream-metric.signal-freshness", lockAtMostFor = "PT30S")
    fun aggregateSignalFreshness() = aggregate("aggregate_signal_freshness")

    @Scheduled(cron = "15 */5 * * * *", scheduler = "streamMetricTaskScheduler")
    @SchedulerLock(name = "s7.stream-metric.dlq-events", lockAtMostFor = "PT30S")
    fun aggregateDlqEvents() = aggregate("aggregate_dlq_events")

    private fun aggregate(functionName: String) {
        try {
            jdbcProvider.getIfAvailable()?.queryForObject("SELECT $functionName()", Boolean::class.java)
        } catch (_: RuntimeException) {
            logger.error("Stream metric aggregation failed closed for {}.", functionName)
        }
    }

    private companion object {
        val logger = LoggerFactory.getLogger(StreamMetricAggregator::class.java)
    }
}
