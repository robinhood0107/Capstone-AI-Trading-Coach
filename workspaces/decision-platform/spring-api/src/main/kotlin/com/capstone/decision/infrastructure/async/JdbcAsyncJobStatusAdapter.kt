package com.capstone.decision.infrastructure.async

import com.capstone.decision.application.async.AsyncJobError
import com.capstone.decision.application.async.AsyncJobPageQuery
import com.capstone.decision.application.async.AsyncJobStatus
import com.capstone.decision.application.async.AsyncJobStatusPort
import com.capstone.decision.application.async.AsyncJobStatusUnavailableException
import com.capstone.decision.application.async.AsyncJobType
import com.capstone.decision.application.async.AsyncJobView
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import java.sql.ResultSet
import java.time.OffsetDateTime
import java.time.ZoneOffset

@Repository
class JdbcAsyncJobStatusAdapter(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
) : AsyncJobStatusPort {
    override fun find(
        actorUserId: String,
        securityVersion: Long,
        jobId: String,
    ): AsyncJobView? =
        protect {
            jdbc()
                .query(
                    "SELECT * FROM read_async_job_status(:actorUserId, :securityVersion, :jobId)",
                    mapOf(
                        "actorUserId" to actorUserId,
                        "securityVersion" to securityVersion,
                        "jobId" to jobId,
                    ),
                ) { result, _ -> result.toView() }
                .singleOrNull()
        }

    override fun list(query: AsyncJobPageQuery): List<AsyncJobView> =
        protect {
            jdbc().query(
                """
                SELECT *
                FROM list_async_job_status(
                  :actorUserId,
                  :securityVersion,
                  :status,
                  :jobType,
                  :beforeRequestedAt,
                  :beforeJobId,
                  :limit
                )
                """.trimIndent(),
                mapOf(
                    "actorUserId" to query.actorUserId,
                    "securityVersion" to query.securityVersion,
                    "status" to query.status?.name,
                    "jobType" to query.type?.name,
                    "beforeRequestedAt" to query.beforeRequestedAt?.let { OffsetDateTime.ofInstant(it, ZoneOffset.UTC) },
                    "beforeJobId" to query.beforeJobId,
                    "limit" to query.size + 1,
                ),
            ) { result, _ -> result.toView() }
        }

    private fun ResultSet.toView(): AsyncJobView {
        val errorCode = getString("error_code")
        val errorClass = getString("error_class")
        return AsyncJobView(
            jobId = getString("job_id"),
            type = AsyncJobType.valueOf(getString("job_type")),
            status = AsyncJobStatus.valueOf(getString("status")),
            requestedAt = getObject("requested_at", OffsetDateTime::class.java).toInstant(),
            startedAt = getObject("started_at", OffsetDateTime::class.java)?.toInstant(),
            completedAt = getObject("completed_at", OffsetDateTime::class.java)?.toInstant(),
            sourceId = getString("source_id"),
            artifactId = getString("artifact_id"),
            resultRef = getString("result_ref"),
            error = if (errorCode == null || errorClass == null) null else AsyncJobError(errorCode, errorClass),
        )
    }

    private fun jdbc(): NamedParameterJdbcTemplate = jdbcProvider.getIfAvailable() ?: throw AsyncJobStatusUnavailableException()

    private fun <T> protect(block: () -> T): T =
        try {
            block()
        } catch (exception: AsyncJobStatusUnavailableException) {
            throw exception
        } catch (exception: RuntimeException) {
            throw AsyncJobStatusUnavailableException(exception)
        }
}
