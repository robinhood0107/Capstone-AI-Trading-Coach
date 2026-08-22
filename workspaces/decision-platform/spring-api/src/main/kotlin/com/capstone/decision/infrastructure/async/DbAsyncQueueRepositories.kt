package com.capstone.decision.infrastructure.async

import org.springframework.beans.factory.ObjectProvider
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import java.time.Instant
import java.time.OffsetDateTime
import java.util.UUID

data class ClaimedOutboxEvent(
    val eventId: String,
    val eventType: String,
    val aggregateId: String,
    val payloadJson: String,
    val partitionKey: String,
    val schemaVersion: Int,
    val claimToken: UUID,
    val attempt: Int,
)

data class ClaimedAsyncJob(
    val jobId: String,
    val jobType: String,
    val payloadJson: String,
    val claimToken: UUID,
    val attempt: Int,
    val hardDeadline: Instant,
)

@Repository
@ConditionalOnProperty(name = ["app.async.adapter"], havingValue = "db", matchIfMissing = true)
class DbAsyncOutboxQueue(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
) {
    fun quarantineUnknown(limit: Int): Int =
        requireNotNull(
            jdbc().jdbcTemplate.queryForObject(
                "SELECT quarantine_unknown_outbox(?)",
                Int::class.java,
                limit,
            ),
        )

    fun claim(
        worker: String,
        limit: Int,
    ): List<ClaimedOutboxEvent> =
        jdbc().jdbcTemplate.query(
            "SELECT * FROM claim_db_async_outbox(?, ?)",
            { statement ->
                statement.setString(1, worker)
                statement.setInt(2, limit)
            },
        ) { result, _ ->
            ClaimedOutboxEvent(
                eventId = result.getString("event_id"),
                eventType = result.getString("event_type"),
                aggregateId = result.getString("aggregate_id"),
                payloadJson = result.getString("payload_json"),
                partitionKey = result.getString("partition_key"),
                schemaVersion = result.getInt("kafka_schema_version"),
                claimToken = result.getObject("claim_token", UUID::class.java),
                attempt = result.getInt("attempt_count"),
            )
        }

    fun complete(event: ClaimedOutboxEvent): Boolean =
        jdbc().jdbcTemplate.queryForObject(
            "SELECT complete_event_outbox(?, ?)",
            Boolean::class.java,
            event.eventId,
            event.claimToken,
        ) == true

    fun fail(
        event: ClaimedOutboxEvent,
        failureCode: String,
        errorClass: String,
    ): String =
        requireNotNull(
            jdbc().jdbcTemplate.queryForObject(
                "SELECT fail_event_outbox(?, ?, ?, ?)",
                String::class.java,
                event.eventId,
                event.claimToken,
                failureCode,
                errorClass,
            ),
        )

    fun quarantine(
        event: ClaimedOutboxEvent,
        failureCode: String,
    ): Boolean =
        jdbc().jdbcTemplate.queryForObject(
            "SELECT quarantine_claimed_outbox(?, ?, ?)",
            Boolean::class.java,
            event.eventId,
            event.claimToken,
            failureCode,
        ) == true

    fun bindPayloadHash(
        event: ClaimedOutboxEvent,
        payloadHash: String,
    ): Boolean =
        jdbc().jdbcTemplate.queryForObject(
            "SELECT bind_claimed_outbox_payload_hash(?, ?, ?)",
            Boolean::class.java,
            event.eventId,
            event.claimToken,
            payloadHash,
        ) == true

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable() ?: throw IllegalStateException("Async outbox JDBC access is unavailable.")
}

@Repository
@ConditionalOnProperty(name = ["app.async.adapter"], havingValue = "db", matchIfMissing = true)
@ConditionalOnProperty(name = ["app.async.worker.enabled"], havingValue = "true")
class DbAsyncWorkerQueue(
    database: AsyncWorkerDatabase,
) {
    private val jdbc = JdbcTemplate(database.dataSource)

    fun claimByEvent(
        worker: String,
        event: ClaimedOutboxEvent,
        payloadHash: String,
    ): ClaimedAsyncJob? =
        jdbc
            .query(
                "SELECT * FROM claim_async_job_by_event(?, ?, ?, ?, ?, ?)",
                { statement ->
                    statement.setString(1, worker)
                    statement.setString(2, event.eventId)
                    statement.setString(3, event.eventType)
                    statement.setString(4, event.aggregateId)
                    statement.setString(5, payloadHash)
                    statement.setString(6, event.partitionKey)
                },
            ) { result, _ ->
                ClaimedAsyncJob(
                    jobId = result.getString("job_id"),
                    jobType = result.getString("job_type"),
                    payloadJson = result.getString("payload_json"),
                    claimToken = result.getObject("claim_token", UUID::class.java),
                    attempt = result.getInt("attempt_count"),
                    hardDeadline = result.getObject("hard_deadline_at", OffsetDateTime::class.java).toInstant(),
                )
            }.singleOrNull()

    fun resolveCompleted(
        event: ClaimedOutboxEvent,
        payloadHash: String,
    ): Boolean =
        jdbc.queryForObject(
            "SELECT resolve_completed_async_event(?, ?, ?, ?, ?)",
            Boolean::class.java,
            event.eventId,
            event.eventType,
            event.aggregateId,
            payloadHash,
            event.partitionKey,
        ) == true

    fun fail(
        job: ClaimedAsyncJob,
        failureCode: String,
        errorClass: String,
    ): String =
        requireNotNull(
            jdbc.queryForObject(
                "SELECT fail_async_job(?, ?, ?, ?)",
                String::class.java,
                job.jobId,
                job.claimToken,
                failureCode,
                errorClass,
            ),
        )
}
