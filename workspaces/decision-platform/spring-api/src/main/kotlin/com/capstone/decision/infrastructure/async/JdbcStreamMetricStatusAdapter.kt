package com.capstone.decision.infrastructure.async

import com.capstone.decision.application.async.DecisionDistribution
import com.capstone.decision.application.async.PipelineHealth
import com.capstone.decision.application.async.StreamMetricComponent
import com.capstone.decision.application.async.StreamMetricComponentStatus
import com.capstone.decision.application.async.StreamMetricStatus
import com.capstone.decision.application.async.StreamMetricStatusPort
import com.capstone.decision.application.async.StreamMetricUnavailableException
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import java.sql.ResultSet
import java.time.OffsetDateTime

@Repository
class JdbcStreamMetricStatusAdapter(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
) : StreamMetricStatusPort {
    override fun read(
        actorUserId: String,
        securityVersion: Long,
    ): StreamMetricStatus? =
        protect {
            jdbc()
                .query(
                    "SELECT * FROM read_stream_metric_status(:actorUserId, :securityVersion)",
                    mapOf("actorUserId" to actorUserId, "securityVersion" to securityVersion),
                ) { result, _ -> result.toStatus() }
                .singleOrNull()
        }

    private fun ResultSet.toStatus() =
        StreamMetricStatus(
            lastUpdatedAt = instant("last_updated_at"),
            pipelineHealth = PipelineHealth.valueOf(getString("pipeline_health")),
            signalStaleRatio = getBigDecimal("stale_signal_ratio"),
            decisionDistribution =
                DecisionDistribution(
                    allow = getLong("allow_count"),
                    warn = getLong("warn_count"),
                    hold = getLong("hold_count"),
                    block = getLong("block_count"),
                ),
            failedJobCount = getLong("failed_job_count"),
            dlqEventCount = getLong("dlq_event_count"),
            decisionComponent = component("decision"),
            signalComponent = component("signal"),
            failedJobComponent = component("failed"),
            dlqComponent = component("dlq"),
        )

    private fun ResultSet.component(prefix: String) =
        StreamMetricComponent(
            status = StreamMetricComponentStatus.valueOf(getString("${prefix}_status")),
            observedAt = instant("${prefix}_observed_at"),
        )

    private fun ResultSet.instant(column: String) = getObject(column, OffsetDateTime::class.java)?.toInstant()

    private fun jdbc(): NamedParameterJdbcTemplate = jdbcProvider.getIfAvailable() ?: throw StreamMetricUnavailableException()

    private fun <T> protect(block: () -> T): T =
        try {
            block()
        } catch (exception: StreamMetricUnavailableException) {
            throw exception
        } catch (exception: RuntimeException) {
            throw StreamMetricUnavailableException(exception)
        }
}
