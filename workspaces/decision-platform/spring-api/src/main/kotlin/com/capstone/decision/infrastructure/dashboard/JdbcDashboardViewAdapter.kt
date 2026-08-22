package com.capstone.decision.infrastructure.dashboard

import com.capstone.decision.application.dashboard.ArtifactIngestStatusView
import com.capstone.decision.application.dashboard.DashboardArtifactKind
import com.capstone.decision.application.dashboard.DashboardUnavailableException
import com.capstone.decision.application.dashboard.DashboardViewPort
import com.capstone.decision.infrastructure.security.ActorCapabilityIssuer
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.sql.ResultSet
import java.time.Clock
import java.time.OffsetDateTime

@Repository
class JdbcDashboardViewAdapter(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val objectMapper: ObjectMapper,
    private val clock: Clock,
    private val actorCapabilityIssuer: ActorCapabilityIssuer,
) : DashboardViewPort {
    override fun artifact(
        actorUserId: String,
        securityVersion: Long,
        kind: DashboardArtifactKind,
        runId: String,
    ): JsonNode? =
        protect {
            jdbc()
                .query(
                    "SELECT * FROM read_dashboard_artifact_view_authorized(:capability,:actor,:version,:kind,:runId)",
                    mapOf(
                        "capability" to actorCapabilityIssuer.issue(actorUserId),
                        "actor" to actorUserId,
                        "version" to securityVersion,
                        "kind" to kind.name,
                        "runId" to runId,
                    ),
                ) { result, _ ->
                    val envelope = objectMapper.readTree(result.getString("projection_json"))
                    require(envelope.path("success").asBoolean() && envelope.path("data").isObject)
                    val embedded = envelope.path("data")
                    val viewState = embedded.path("viewState").stringValue()
                    require(viewState in setOf("READY", "EMPTY", "STALE"))
                    node(
                        mapOf(
                            "viewState" to viewState,
                            "asOf" to result.instant("as_of").toString(),
                            "freshUntil" to result.instant("fresh_until").toString(),
                            "evidenceMode" to result.getString("evidence_mode"),
                            "performanceClaimAllowed" to false,
                            "view" to embedded.path("view"),
                        ),
                    )
                }.singleOrNull()
        }

    override fun risk(
        actorUserId: String,
        securityVersion: Long,
        decisionId: String,
    ): JsonNode? =
        protect {
            jdbc()
                .query(
                    "SELECT * FROM read_dashboard_risk_view_authorized(:capability,:actor,:version,:decisionId)",
                    mapOf(
                        "capability" to actorCapabilityIssuer.issue(actorUserId),
                        "actor" to actorUserId,
                        "version" to securityVersion,
                        "decisionId" to decisionId,
                    ),
                ) { result, _ ->
                    val asOf = result.instant("evaluation_as_of")
                    val freshUntil = result.instant("valid_until")
                    node(
                        mapOf(
                            "viewState" to if (freshUntil.isBefore(clock.instant())) "STALE" else "READY",
                            "asOf" to asOf.toString(),
                            "freshUntil" to freshUntil.toString(),
                            "evidenceMode" to "STORED_RUNTIME",
                            "performanceClaimAllowed" to false,
                            "view" to
                                mapOf(
                                    "decisionId" to result.getString("decision_id"),
                                    "action" to result.getString("outcome"),
                                    "reasons" to json(result, "reasons"),
                                    "principles" to json(result, "principles"),
                                    "riskItems" to json(result, "risk_items"),
                                ),
                        ),
                    )
                }.singleOrNull()
        }

    override fun rag(
        actorUserId: String,
        securityVersion: Long,
        answerId: String,
    ): JsonNode? =
        protect {
            jdbc()
                .query(
                    "SELECT * FROM read_dashboard_rag_sources_authorized(:capability,:actor,:version,:answerId)",
                    mapOf(
                        "capability" to actorCapabilityIssuer.issue(actorUserId),
                        "actor" to actorUserId,
                        "version" to securityVersion,
                        "answerId" to answerId,
                    ),
                ) { result, _ ->
                    val createdAt = result.instant("created_at")
                    val expiresAt = result.instant("expires_at")
                    val sources = json(result, "sources")
                    node(
                        mapOf(
                            "viewState" to if (expiresAt.isBefore(clock.instant())) "STALE" else "READY",
                            "asOf" to createdAt.toString(),
                            "freshUntil" to expiresAt.toString(),
                            "evidenceMode" to "STORED_RUNTIME",
                            "performanceClaimAllowed" to false,
                            "view" to
                                mapOf(
                                    "answerId" to result.getString("answer_id"),
                                    "topSources" to sources.take(3),
                                    "expandableSources" to sources.take(5),
                                ),
                        ),
                    )
                }.singleOrNull()
        }

    override fun artifactStatuses(
        actorUserId: String,
        securityVersion: Long,
    ): List<ArtifactIngestStatusView>? =
        protect {
            jdbc().query(
                "SELECT * FROM list_artifact_ingest_status_authorized(:capability,:actor,:version)",
                mapOf(
                    "capability" to actorCapabilityIssuer.issue(actorUserId),
                    "actor" to actorUserId,
                    "version" to securityVersion,
                ),
            ) { result, _ ->
                ArtifactIngestStatusView(
                    artifactId = result.getString("artifact_id"),
                    fileName = result.getString("file_name"),
                    producer = result.getString("producer"),
                    runId = result.getString("run_id"),
                    fileHash = result.getString("file_hash"),
                    schemaVersion = result.getString("schema_version"),
                    status = result.getString("status"),
                    lastIngestedAt = result.getObject("last_ingested_at", OffsetDateTime::class.java)?.toInstant(),
                    duplicate = result.getBoolean("duplicate"),
                )
            }
        }

    private fun ResultSet.instant(column: String) = getObject(column, OffsetDateTime::class.java).toInstant()

    private fun json(
        result: ResultSet,
        column: String,
    ): JsonNode = objectMapper.readTree(result.getString(column))

    private fun node(value: Any): JsonNode = objectMapper.readTree(objectMapper.writeValueAsString(value))

    private fun jdbc(): NamedParameterJdbcTemplate = jdbcProvider.getIfAvailable() ?: throw DashboardUnavailableException()

    private fun <T> protect(block: () -> T): T =
        try {
            block()
        } catch (exception: DashboardUnavailableException) {
            throw exception
        } catch (exception: RuntimeException) {
            throw DashboardUnavailableException(exception)
        }
}
