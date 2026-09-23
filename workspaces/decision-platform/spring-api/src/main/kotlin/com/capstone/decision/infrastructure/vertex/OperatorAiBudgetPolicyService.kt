package com.capstone.decision.infrastructure.vertex

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.application.security.AppPrincipal
import org.springframework.beans.factory.ObjectProvider
import org.springframework.beans.factory.annotation.Value
import org.springframework.context.annotation.Profile
import org.springframework.dao.PessimisticLockingFailureException
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional

data class OperatorAiBudgetPolicy(
    val hardCapCents: Long,
    val dailySoftCapCents: Long,
    val revision: Long,
)

/** The private NAS value is immutable at runtime; only ADMIN may lower its database-backed soft cap. */
@Service
@Profile("mars-full", "mars-demo")
class OperatorAiBudgetPolicyService(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    @Value("\${MARS_AI_DAILY_HARD_CAP_USD:}") rawHardCapUsd: String,
) {
    private val hardCapCents = parseHardCap(rawHardCapUsd)

    /**
     * A successful reservation is never refunded because provider billing can be uncertain.
     * V201 permits a null owner only for DEMO_AGENT; authenticated sources must supply their owner.
     */
    fun reserveGrossUsage(
        reservationId: String,
        ownerUserId: String?,
        source: String,
        provider: String,
        maxGrossMicrousd: Long,
    ) {
        val accepted =
            jdbc().queryForObject(
                "SELECT reserve_operator_ai_gross_usage_v1(:id, :owner, :source, :provider, :cost, :hardCap)",
                mapOf(
                    "id" to reservationId,
                    "owner" to ownerUserId,
                    "source" to source,
                    "provider" to provider,
                    "cost" to maxGrossMicrousd,
                    "hardCap" to Math.multiplyExact(hardCapCents, MICROUSD_PER_CENT),
                ),
                Boolean::class.java,
            ) == true
        if (!accepted) throw IllegalStateException("OPERATOR_AI_DAILY_GROSS_BUDGET_EXHAUSTED")
    }

    @Transactional(readOnly = true)
    fun read(actor: AppPrincipal): OperatorAiBudgetPolicy {
        requireAdmin(actor)
        return jdbc()
            .query(
                "SELECT * FROM read_operator_ai_budget_policy_v1(:actor, :version)",
                mapOf("actor" to actor.userId, "version" to actor.securityVersion),
            ) { row, _ ->
                OperatorAiBudgetPolicy(
                    hardCapCents = hardCapCents,
                    dailySoftCapCents = row.getLong("daily_soft_cap_microusd") / MICROUSD_PER_CENT,
                    revision = row.getLong("revision"),
                )
            }.single()
    }

    @Transactional
    fun update(
        actor: AppPrincipal,
        dailySoftCapCents: Long,
        expectedRevision: Long,
    ): OperatorAiBudgetPolicy {
        requireAdmin(actor)
        if (dailySoftCapCents !in 0..hardCapCents || expectedRevision <= 0) {
            throw ApiException(ErrorCode.VALIDATION_ERROR)
        }
        val revision =
            try {
                jdbc().queryForObject(
                    "SELECT set_operator_ai_budget_policy_v1(:actor, :version, :cap, :revision)",
                    mapOf(
                        "actor" to actor.userId,
                        "version" to actor.securityVersion,
                        "cap" to Math.multiplyExact(dailySoftCapCents, MICROUSD_PER_CENT),
                        "revision" to expectedRevision,
                    ),
                    Long::class.java,
                ) ?: error("OPERATOR_AI_BUDGET_UPDATE_UNAVAILABLE")
            } catch (_: PessimisticLockingFailureException) {
                throw ApiException(ErrorCode.CONFLICT)
            }
        return OperatorAiBudgetPolicy(hardCapCents, dailySoftCapCents, revision)
    }

    private fun requireAdmin(actor: AppPrincipal) {
        if (actor.role != "ADMIN") {
            throw org.springframework.security.access
                .AccessDeniedException("ADMIN required")
        }
    }

    private fun jdbc(): NamedParameterJdbcTemplate = jdbcProvider.getIfAvailable() ?: error("OPERATOR_AI_BUDGET_DATABASE_UNAVAILABLE")

    private fun parseHardCap(value: String): Long {
        require(HARD_CAP_PATTERN.matches(value)) { "MARS_AI_DAILY_HARD_CAP_USD must be set for a public MARS product." }
        val cents = value.toBigDecimal().movePointRight(2).longValueExact()
        require(cents > 0) { "MARS_AI_DAILY_HARD_CAP_USD must be positive." }
        return cents
    }

    private companion object {
        const val MICROUSD_PER_CENT = 10_000L
        val HARD_CAP_PATTERN = Regex("^[0-9]{1,8}(\\.[0-9]{1,2})?$")
    }
}
