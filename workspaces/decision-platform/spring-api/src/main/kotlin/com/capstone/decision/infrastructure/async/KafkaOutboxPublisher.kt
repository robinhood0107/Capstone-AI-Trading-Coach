package com.capstone.decision.infrastructure.async

import org.apache.kafka.clients.producer.ProducerRecord
import org.apache.kafka.common.header.internals.RecordHeaders
import org.slf4j.LoggerFactory
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.kafka.core.KafkaTemplate
import org.springframework.stereotype.Component
import tools.jackson.databind.ObjectMapper
import tools.jackson.databind.node.ObjectNode
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.OffsetDateTime
import java.util.HexFormat
import java.util.UUID
import java.util.concurrent.TimeUnit

data class KafkaClaimedEvent(
    val storageEventId: String,
    val eventId: String,
    val eventType: String,
    val aggregateId: String,
    val partitionKey: String,
    val payloadJson: String,
    val occurredAt: OffsetDateTime,
    val schemaVersion: Int,
    val topicName: String,
    val claimToken: UUID,
    val attempt: Int,
    val dlq: Boolean,
)

@Component
@ConditionalOnProperty(name = ["app.async.adapter"], havingValue = "kafka")
class KafkaOutboxQueue(
    private val jdbc: JdbcTemplate,
) {
    fun quarantineUnknown(limit: Int): Int =
        requireNotNull(jdbc.queryForObject("SELECT quarantine_unknown_outbox(?)", Int::class.java, limit))

    fun claim(
        worker: String,
        limit: Int,
    ): List<KafkaClaimedEvent> =
        jdbc.query(
            "SELECT * FROM claim_event_outbox(?, ?)",
            { statement ->
                statement.setString(1, worker)
                statement.setInt(2, limit)
            },
        ) { result, _ ->
            KafkaClaimedEvent(
                storageEventId = result.getString("event_id"),
                eventId = result.getString("event_id"),
                eventType = result.getString("event_type"),
                aggregateId = result.getString("aggregate_id"),
                partitionKey = result.getString("partition_key"),
                payloadJson = result.getString("payload_json"),
                occurredAt = result.getObject("occurred_at", OffsetDateTime::class.java),
                schemaVersion = result.getInt("kafka_schema_version"),
                topicName = result.getString("topic_name"),
                claimToken = result.getObject("claim_token", UUID::class.java),
                attempt = result.getInt("attempt_count"),
                dlq = false,
            )
        }

    fun claimDlq(
        worker: String,
        limit: Int,
    ): List<KafkaClaimedEvent> =
        jdbc.query(
            "SELECT * FROM claim_dlq_outbox(?, ?)",
            { statement ->
                statement.setString(1, worker)
                statement.setInt(2, limit)
            },
        ) { result, _ ->
            KafkaClaimedEvent(
                storageEventId = result.getString("storage_event_id"),
                eventId = result.getString("event_id"),
                eventType = result.getString("event_type"),
                aggregateId = result.getString("aggregate_id"),
                partitionKey = result.getString("partition_key"),
                payloadJson = result.getString("payload_json"),
                occurredAt = result.getObject("occurred_at", OffsetDateTime::class.java),
                schemaVersion = result.getInt("kafka_schema_version"),
                topicName = result.getString("topic_name"),
                claimToken = result.getObject("claim_token", UUID::class.java),
                attempt = result.getInt("attempt_count"),
                dlq = true,
            )
        }

    fun complete(event: KafkaClaimedEvent): Boolean =
        jdbc.queryForObject(
            if (event.dlq) "SELECT complete_dlq_outbox(?, ?)" else "SELECT complete_event_outbox(?, ?)",
            Boolean::class.java,
            event.storageEventId,
            event.claimToken,
        ) == true

    fun fail(
        event: KafkaClaimedEvent,
        code: String,
    ): String {
        if (event.dlq) {
            val changed =
                jdbc.queryForObject(
                    "SELECT fail_dlq_outbox(?, ?)",
                    Boolean::class.java,
                    event.storageEventId,
                    event.claimToken,
                ) == true
            return if (changed) "DLQ_REQUESTED" else "CONFLICT"
        }
        return requireNotNull(
            jdbc.queryForObject(
                "SELECT fail_event_outbox(?, ?, ?, 'RETRYABLE_TRANSIENT')",
                String::class.java,
                event.storageEventId,
                event.claimToken,
                code,
            ),
        )
    }

    fun quarantine(
        event: KafkaClaimedEvent,
        code: String,
    ): Boolean =
        jdbc.queryForObject(
            "SELECT quarantine_claimed_outbox(?, ?, ?)",
            Boolean::class.java,
            event.storageEventId,
            event.claimToken,
            code,
        ) == true

    fun bindPayloadHash(
        event: KafkaClaimedEvent,
        payloadHash: String,
    ): Boolean =
        jdbc.queryForObject(
            "SELECT bind_claimed_outbox_payload_hash(?, ?, ?)",
            Boolean::class.java,
            event.storageEventId,
            event.claimToken,
            payloadHash,
        ) == true
}

@Component
@ConditionalOnProperty(name = ["app.async.adapter"], havingValue = "kafka")
class KafkaOutboxPublisher(
    private val queue: KafkaOutboxQueue,
    private val kafkaTemplate: KafkaTemplate<String, String>,
    private val objectMapper: ObjectMapper,
    private val catalog: AsyncTopicCatalog,
    private val asyncProperties: AsyncProperties,
    private val kafkaProperties: KafkaAsyncProperties,
) {
    fun poll() {
        try {
            queue.quarantineUnknown(asyncProperties.claimPageSize)
            queue.claim(WORKER_ID, asyncProperties.claimPageSize).forEach(::publish)
            queue.claimDlq(WORKER_ID, asyncProperties.claimPageSize).forEach(::publish)
        } catch (_: RuntimeException) {
            logger.error("S7 Kafka outbox poll failed closed.")
        }
    }

    private fun publish(event: KafkaClaimedEvent) {
        try {
            if (event.dlq) catalog.requireTopic(event.topicName) else catalog.requireBaseTopic(event.topicName)
            require(event.schemaVersion == 1 && event.attempt in 1..3)
            require(PARTITION_KEY.matches(event.partitionKey))
            val storedReferences = objectMapper.readTree(event.payloadJson)
            require(storedReferences.isObject && storedReferences.size() <= 64)
            val references = storedReferences.deepCopy() as ObjectNode
            references.remove("ownerRef")
            val canonicalReferences = objectMapper.writeValueAsBytes(references)
            require(canonicalReferences.size <= 32_768)
            val payloadHash = "sha256:${sha256(canonicalReferences)}"
            if (!event.dlq) require(queue.bindPayloadHash(event, payloadHash))
            val envelope =
                linkedMapOf(
                    "eventId" to event.eventId,
                    "eventType" to event.eventType,
                    "schemaVersion" to event.schemaVersion,
                    "occurredAt" to event.occurredAt.toInstant().toString(),
                    "partitionKey" to event.partitionKey,
                    "payloadHash" to payloadHash,
                    "references" to references,
                )
            val value = objectMapper.writeValueAsString(envelope)
            require(value.toByteArray(StandardCharsets.UTF_8).size <= 65_536)
            val headers =
                RecordHeaders()
                    .add("event-type", event.eventType.toByteArray(StandardCharsets.US_ASCII))
                    .add("schema-version", event.schemaVersion.toString().toByteArray(StandardCharsets.US_ASCII))
                    .add("attempt", event.attempt.toString().toByteArray(StandardCharsets.US_ASCII))
            val record =
                ProducerRecord<String, String>(
                    event.topicName,
                    null,
                    null,
                    event.partitionKey,
                    value,
                    headers,
                )
            kafkaTemplate.send(record).get(kafkaProperties.publishTimeout.toMillis(), TimeUnit.MILLISECONDS)
            if (!queue.complete(event)) logger.warn("S7 Kafka publish succeeded but DB completion was fenced.")
        } catch (_: IllegalArgumentException) {
            if (event.dlq) {
                queue.fail(event, "INVALID_EVENT_PAYLOAD")
            } else {
                queue.quarantine(event, "INVALID_EVENT_PAYLOAD")
            }
        } catch (_: RuntimeException) {
            queue.fail(event, "KAFKA_PUBLISH_FAILED")
        } catch (_: Exception) {
            queue.fail(event, "KAFKA_PUBLISH_FAILED")
        }
    }

    private fun sha256(value: ByteArray): String = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value))

    private companion object {
        const val WORKER_ID = "spring-kafka-publisher"
        val PARTITION_KEY = Regex("^hmac-sha256:[0-9a-f]{64}$")
        val logger = LoggerFactory.getLogger(KafkaOutboxPublisher::class.java)
    }
}
