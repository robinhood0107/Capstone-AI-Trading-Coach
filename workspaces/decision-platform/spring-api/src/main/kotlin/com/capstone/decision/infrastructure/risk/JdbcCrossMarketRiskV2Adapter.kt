package com.capstone.decision.infrastructure.risk

import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketAvailability
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketDecisionInput
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketEvidenceMode
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketExposure
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketExposureClassification
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketInputUnavailableException
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketRiskPort
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketRiskSnapshot
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketRuntimeMode
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketStorageMode
import com.capstone.decision.application.risk.port.EvaluationSourceRequest
import org.springframework.stereotype.Repository
import java.time.OffsetDateTime
import java.util.UUID

@Repository
class JdbcCrossMarketRiskV2Adapter(
    private val actorScopedReadQuery: ActorScopedReadQuery,
) : CrossMarketRiskPort {
    override fun load(request: EvaluationSourceRequest): CrossMarketDecisionInput {
        val rows =
            actorScopedReadQuery.query(
                actorUserId = request.actorUserId,
                sql =
                    """
                    SELECT * FROM read_cross_market_decision_input_v2(?, ?, ?::timestamptz)
                    """.trimIndent(),
                binder = { statement ->
                    statement.setString(1, request.portfolioContext.ownerScopeHash)
                    statement.setString(2, request.orderIntent.symbol)
                    statement.setObject(3, OffsetDateTime.ofInstant(request.evaluationAsOf, java.time.ZoneOffset.UTC))
                },
            ) { result ->
                val availableAt = result.getObject("available_at", OffsetDateTime::class.java).toInstant()
                CrossMarketDecisionInput(
                    snapshot =
                        CrossMarketRiskSnapshot(
                            snapshotId = result.getObject("snapshot_id", UUID::class.java),
                            ownerScopeHash = result.getString("owner_scope_hash"),
                            symbol = result.getString("symbol"),
                            availableAt = availableAt,
                            staleAt = result.getObject("stale_at", OffsetDateTime::class.java).toInstant(),
                            evidenceMode = CrossMarketEvidenceMode.valueOf(result.getString("evidence_mode")),
                            storageMode = CrossMarketStorageMode.valueOf(result.getString("storage_mode")),
                            runtimeMode = CrossMarketRuntimeMode.valueOf(result.getString("runtime_mode")),
                            availability = CrossMarketAvailability.valueOf(result.getString("availability")),
                            score = result.getBigDecimal("score"),
                            thresholdPercentile = result.getBigDecimal("threshold_percentile"),
                            thresholdArtifactHash = result.getString("threshold_artifact_hash"),
                            configHash = result.getString("config_hash"),
                            semanticInputHash = result.getString("semantic_input_hash"),
                            artifactHash = result.getString("artifact_hash"),
                        ),
                    exposure =
                        CrossMarketExposure(
                            symbol = result.getString("symbol"),
                            classification =
                                CrossMarketExposureClassification.valueOf(
                                    result.getString("exposure_classification"),
                                ),
                            availableAt =
                                result.getObject("exposure_available_at", OffsetDateTime::class.java).toInstant(),
                            catalogHash = result.getString("exposure_catalog_hash"),
                        ),
                )
            }
        if (rows.size != 1) {
            throw CrossMarketInputUnavailableException(if (rows.isEmpty()) "MISSING" else "DUPLICATE")
        }
        return rows.single()
    }
}
