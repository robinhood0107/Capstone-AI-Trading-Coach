package com.capstone.decision.infrastructure.async

import com.capstone.decision.application.async.AcceptedAsyncJob
import com.capstone.decision.application.async.AsyncJobRequest
import com.capstone.decision.application.async.AsyncJobRequestConflictException
import com.capstone.decision.application.async.AsyncJobType
import com.capstone.decision.application.async.AsyncPipelinePort
import com.capstone.decision.infrastructure.security.ActorCapabilityIssuer
import org.springframework.beans.factory.ObjectProvider
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Component
import org.springframework.transaction.annotation.Transactional
import tools.jackson.databind.ObjectMapper
import java.nio.charset.StandardCharsets
import java.util.HexFormat
import java.util.UUID
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

@Component
class JdbcAsyncRequestWriter(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val objectMapper: ObjectMapper,
    private val actorCapabilityIssuer: ActorCapabilityIssuer,
    properties: AsyncProperties,
) {
    private val partitionKey = properties.partitionHmacKey.toByteArray(StandardCharsets.UTF_8)

    @Transactional
    fun request(command: AsyncJobRequest): AcceptedAsyncJob {
        validate(command)
        val jobId = "job_${opaqueId()}"
        val eventId = "evt_${opaqueId()}"
        val payload = linkedMapOf("jobId" to jobId, "ownerRef" to command.requestedBy) + command.references
        val payloadJson = objectMapper.writeValueAsString(payload)
        require(payloadJson.toByteArray(StandardCharsets.UTF_8).size <= DB_PAYLOAD_BYTES)
        val eventType = EVENT_TYPES.getValue(command.type)
        val created =
            jdbc().queryForObject(
                """
                SELECT create_async_request_authorized(
                  :capability,:eventId,:eventType,:partitionKey,
                  :jobId,:jobType,:requestedBy,CAST(:payload AS jsonb)
                )
                """.trimIndent(),
                mapOf(
                    "capability" to actorCapabilityIssuer.issue(command.requestedBy),
                    "eventId" to eventId,
                    "eventType" to eventType,
                    "partitionKey" to opaquePartitionKey(command.requestedBy, command.type),
                    "jobId" to jobId,
                    "jobType" to command.type.name,
                    "requestedBy" to command.requestedBy,
                    "payload" to payloadJson,
                ),
                Boolean::class.java,
            ) == true
        if (!created) throw AsyncJobRequestConflictException()
        return AcceptedAsyncJob(jobId, eventId)
    }

    private fun validate(command: AsyncJobRequest) {
        require(USER_ID.matches(command.requestedBy))
        require(command.references.size <= MAX_REFERENCE_KEYS)
        require(command.references.keys.all(ALLOWED_REFERENCES::contains))
        command.references.forEach { (key, value) ->
            require(value.toByteArray(StandardCharsets.UTF_8).size <= MAX_REFERENCE_BYTES)
            require(REFERENCE_PATTERNS.getValue(key).matches(value))
        }
        when (command.type) {
            AsyncJobType.RAG_INDEX ->
                require(
                    setOf("sourceRevisionId", "sourceId", "importTicketId", "profileId")
                        .all(command.references::containsKey),
                )
            AsyncJobType.ARTIFACT_INGEST -> require("artifactId" in command.references && "contentHash" in command.references)
            AsyncJobType.MODEL_EVAL -> require("runId" in command.references && "contentHash" in command.references)
        }
    }

    private fun opaquePartitionKey(
        requestedBy: String,
        type: AsyncJobType,
    ): String {
        val digest =
            Mac
                .getInstance(HMAC_SHA256)
                .apply { init(SecretKeySpec(partitionKey, HMAC_SHA256)) }
                .doFinal("s7:${type.name}:$requestedBy".toByteArray(StandardCharsets.UTF_8))
        return "hmac-sha256:${HexFormat.of().formatHex(digest)}"
    }

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable() ?: throw IllegalStateException("Async JDBC access is unavailable.")

    private fun opaqueId(): String = UUID.randomUUID().toString().replace("-", "")

    private companion object {
        const val DB_PAYLOAD_BYTES = 32_768
        const val MAX_REFERENCE_KEYS = 8
        const val MAX_REFERENCE_BYTES = 2_048
        const val HMAC_SHA256 = "HmacSHA256"
        val USER_ID = Regex("^usr_[A-Za-z0-9_-]{8,64}$")
        val ALLOWED_REFERENCES =
            setOf(
                "sourceId",
                "sourceRevisionId",
                "importTicketId",
                "profileId",
                "artifactId",
                "runId",
                "contentHash",
                "resultRef",
                "replayOf",
            )
        val REFERENCE_PATTERNS =
            mapOf(
                "sourceId" to Regex("^src_[A-Za-z0-9_-]{8,96}$"),
                "sourceRevisionId" to Regex("^srv_[a-z0-9][a-z0-9_-]{2,95}$"),
                "importTicketId" to Regex("^rti_[0-9a-f]{32}$"),
                "profileId" to Regex("^(bge_m3_local_1024_v1|voyage_context_4_1024_v1)$"),
                "artifactId" to Regex("^artifact_[A-Za-z0-9_-]{8,96}$"),
                "runId" to Regex("^(run|demo)_[A-Za-z0-9_-]{8,96}$"),
                "contentHash" to Regex("^sha256:[0-9a-f]{64}$"),
                "resultRef" to Regex("^[A-Za-z][A-Za-z0-9_-]{7,127}$"),
                "replayOf" to Regex("^evt_[A-Za-z0-9_-]{8,96}$"),
            )
        val EVENT_TYPES =
            mapOf(
                AsyncJobType.RAG_INDEX to "rag.index-requested.v1",
                AsyncJobType.ARTIFACT_INGEST to "artifact.ingest-requested.v1",
                AsyncJobType.MODEL_EVAL to "model.eval-requested.v1",
            )
    }
}

@Component
@ConditionalOnProperty(name = ["app.async.adapter"], havingValue = "db", matchIfMissing = true)
class DbAsyncPipelineAdapter(
    private val writer: JdbcAsyncRequestWriter,
) : AsyncPipelinePort {
    override fun request(command: AsyncJobRequest): AcceptedAsyncJob = writer.request(command)
}
