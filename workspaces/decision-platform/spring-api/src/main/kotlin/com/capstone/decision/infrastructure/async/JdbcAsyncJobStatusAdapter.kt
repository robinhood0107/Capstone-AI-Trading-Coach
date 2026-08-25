package com.capstone.decision.infrastructure.async

import com.capstone.decision.application.async.AsyncJobError
import com.capstone.decision.application.async.AsyncJobPageQuery
import com.capstone.decision.application.async.AsyncJobStatus
import com.capstone.decision.application.async.AsyncJobStatusPort
import com.capstone.decision.application.async.AsyncJobStatusUnavailableException
import com.capstone.decision.application.async.AsyncJobType
import com.capstone.decision.application.async.AsyncJobView
import com.capstone.decision.application.security.AuthenticatedActorRef
import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityIssuer
import com.capstone.decision.infrastructure.security.ActorCapabilityRolePolicy
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import java.sql.ResultSet
import java.time.OffsetDateTime
import java.time.ZoneOffset

@Repository
class JdbcAsyncJobStatusAdapter(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val actorCapabilityIssuer: ActorCapabilityIssuer,
) : AsyncJobStatusPort {
    override fun find(
        actorUserId: String,
        securityVersion: Long,
        jobId: String,
    ): AsyncJobView? =
        protect {
            val binding =
                ActorCapabilityBinding.target(
                    "READ_ASYNC_JOB",
                    "ASYNC_JOB",
                    jobId,
                    ActorCapabilityRolePolicy.ADMIN_ONLY,
                )
            jdbc()
                .query(
                    "SELECT * FROM read_async_job_status_authorized(:capability,:actorUserId,:securityVersion,:jobId)",
                    mapOf(
                        "capability" to actorCapabilityIssuer.issue(AuthenticatedActorRef.current(actorUserId, securityVersion), binding),
                        "actorUserId" to actorUserId,
                        "securityVersion" to securityVersion,
                        "jobId" to jobId,
                    ),
                ) { result, _ -> result.toView() }
                .singleOrNull()
        }

    override fun list(query: AsyncJobPageQuery): List<AsyncJobView> =
        protect {
            val binding =
                ActorCapabilityBinding.request(
                    "LIST_ASYNC_JOBS",
                    "ASYNC_JOB_LIST",
                    "async-jobs",
                    ActorCapabilityRolePolicy.ADMIN_ONLY,
                    query.status?.name,
                    query.type?.name,
                    query.beforeRequestedAt?.toEpochMilli()?.toString(),
                    query.beforeJobId,
                    (query.size + 1).toString(),
                )
            jdbc().query(
                """
                SELECT *
                FROM list_async_job_status_authorized(
                  :capability,
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
                    "capability" to
                        actorCapabilityIssuer.issue(
                            AuthenticatedActorRef.current(query.actorUserId, query.securityVersion),
                            binding,
                        ),
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
