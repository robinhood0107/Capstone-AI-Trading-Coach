package com.capstone.decision.infrastructure.risk

import com.capstone.decision.application.risk.port.ActivePrincipleSnapshot
import com.capstone.decision.application.risk.port.PrincipleSnapshotPort
import com.capstone.decision.application.security.AuthenticatedActorRef
import com.capstone.decision.domain.principle.PrincipleId
import com.capstone.decision.domain.principle.PrincipleMode
import com.capstone.decision.domain.principle.PrincipleStatus
import com.capstone.decision.domain.principle.PrincipleVersionId
import com.capstone.decision.infrastructure.principle.PrincipleRuleJsonCodec
import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityIssuer
import com.capstone.decision.infrastructure.security.ActorCapabilityRolePolicy
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository

// S2.2에서 허용된 유일한 production source adapter이며 owner+ACTIVE+immutable version을 한 SQL로 고정한다.
@Repository
class JdbcPrincipleSnapshotAdapter(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val ruleJsonCodec: PrincipleRuleJsonCodec,
    private val actorCapabilityIssuer: ActorCapabilityIssuer,
) : PrincipleSnapshotPort {
    override fun findActiveOwned(
        actorUserId: String,
        principleId: PrincipleId,
    ): ActivePrincipleSnapshot? =
        jdbc()
            .query(
                """
                SELECT * FROM read_active_owned_principle_snapshot_authorized(
                  :capability, :actorUserId, :principleId
                )
                """.trimIndent(),
                mapOf(
                    "capability" to
                        actorCapabilityIssuer.issue(
                            AuthenticatedActorRef.current(actorUserId),
                            ActorCapabilityBinding.target(
                                "READ_ACTIVE_PRINCIPLE",
                                "PRINCIPLE",
                                principleId.value,
                                ActorCapabilityRolePolicy.OWNER,
                            ),
                        ),
                    "principleId" to principleId.value,
                    "actorUserId" to actorUserId,
                ),
            ) { result, _ ->
                check(result.getString("status") == PrincipleStatus.ACTIVE.name) {
                    "Active Principle pointer referenced a non-active immutable version."
                }
                ActivePrincipleSnapshot(
                    principleId = PrincipleId(result.getString("principle_id")),
                    principleVersionId = PrincipleVersionId(result.getString("principle_version_id")),
                    version = result.getInt("version"),
                    mode = PrincipleMode.valueOf(result.getString("mode")),
                    rules = ruleJsonCodec.decode(result.getString("rules_json")),
                )
            }.singleOrNull()

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable()
            ?: error("S2.2 Principle snapshot JDBC access is unavailable without a configured DataSource.")
}
