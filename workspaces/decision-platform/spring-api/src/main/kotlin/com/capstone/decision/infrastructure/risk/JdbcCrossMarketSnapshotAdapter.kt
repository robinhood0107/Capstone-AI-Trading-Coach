package com.capstone.decision.infrastructure.risk

import com.capstone.decision.application.risk.crossmarket.CrossMarketRiskSnapshot
import com.capstone.decision.application.risk.crossmarket.CrossMarketSnapshotReadPort
import com.capstone.decision.application.risk.crossmarket.CrossMarketSnapshotReadRequest
import com.capstone.decision.application.risk.crossmarket.CrossMarketSnapshotReadResult
import com.capstone.decision.application.risk.crossmarket.CrossMarketSnapshotUnavailableReason
import org.springframework.stereotype.Repository
import java.time.OffsetDateTime

/**
 * actor GUC가 적용되는 별도 read-only transaction에서 V23 bounded view만 조회한다.
 * snapshot을 생성하거나 기존 deterministic RiskEngine 입력에 연결하지 않는다.
 */
@Repository
class JdbcCrossMarketSnapshotAdapter(
    private val actorScopedReadQuery: ActorScopedReadQuery,
) : CrossMarketSnapshotReadPort {
    override fun load(request: CrossMarketSnapshotReadRequest): CrossMarketSnapshotReadResult {
        val rows =
            actorScopedReadQuery.query(
                actorUserId = request.actorUserId,
                sql =
                    """
                    SELECT logical_identity_hash,
                           owner_scope_hash,
                           config_version,
                           availability,
                           evidence_mode,
                           snapshot_available_at,
                           decision_authority,
                           order_authority,
                           validation_status,
                           artifact_hash,
                           payload_json::text AS payload_json
                    FROM latest_cross_market_risk_snapshots
                    WHERE owner_scope_hash = ?
                      AND config_version = ?
                    LIMIT 2
                    """.trimIndent(),
                binder = { statement ->
                    statement.setString(1, request.ownerScopeHash)
                    statement.setString(2, request.configVersion)
                },
            ) { result ->
                CrossMarketRiskSnapshot(
                    logicalIdentityHash = result.getString("logical_identity_hash"),
                    ownerScopeHash = result.getString("owner_scope_hash"),
                    configVersion = result.getString("config_version"),
                    availability = result.getString("availability"),
                    evidenceMode = result.getString("evidence_mode"),
                    snapshotAvailableAt =
                        result
                            .getObject("snapshot_available_at", OffsetDateTime::class.java)
                            .toInstant(),
                    decisionAuthority = result.getString("decision_authority"),
                    orderAuthority = result.getString("order_authority"),
                    validationStatus = result.getString("validation_status"),
                    artifactHash = result.getString("artifact_hash"),
                    canonicalPayloadJson = result.getString("payload_json"),
                )
            }
        if (rows.isEmpty()) {
            return CrossMarketSnapshotReadResult.Unavailable(CrossMarketSnapshotUnavailableReason.MISSING)
        }
        if (rows.size != 1) {
            return CrossMarketSnapshotReadResult.Unavailable(CrossMarketSnapshotUnavailableReason.DUPLICATE)
        }
        val snapshot = rows.single()
        if (snapshot.snapshotAvailableAt.isAfter(request.evaluationAsOf)) {
            return CrossMarketSnapshotReadResult.Unavailable(CrossMarketSnapshotUnavailableReason.FUTURE)
        }
        if (snapshot.availability != "AVAILABLE") {
            return CrossMarketSnapshotReadResult.Unavailable(CrossMarketSnapshotUnavailableReason.SOURCE_UNAVAILABLE)
        }
        return CrossMarketSnapshotReadResult.Available(snapshot)
    }
}
