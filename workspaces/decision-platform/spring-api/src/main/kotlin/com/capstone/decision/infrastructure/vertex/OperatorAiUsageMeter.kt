package com.capstone.decision.infrastructure.vertex

import org.slf4j.LoggerFactory
import org.springframework.beans.factory.ObjectProvider
import org.springframework.context.annotation.Profile
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.transaction.PlatformTransactionManager
import org.springframework.transaction.TransactionDefinition
import org.springframework.transaction.support.TransactionTemplate

/** Records estimated provider exposure without placing a usage or cost gate in front of a call. */
@Service
@Profile("mars-full", "mars-demo")
class OperatorAiUsageMeter(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    transactionManager: PlatformTransactionManager,
) {
    private val requiresNew =
        TransactionTemplate(transactionManager).apply {
            propagationBehavior = TransactionDefinition.PROPAGATION_REQUIRES_NEW
            timeout = 2
        }

    /** Meter writes use their own transaction and can never reject the provider operation. */
    fun recordGrossEstimate(
        reservationId: String,
        ownerUserId: String?,
        source: String,
        provider: String,
        maxGrossMicrousd: Long,
    ) {
        if (maxGrossMicrousd <= 0) return
        try {
            val jdbc = jdbcProvider.getIfAvailable() ?: return
            requiresNew.executeWithoutResult {
                jdbc.jdbcTemplate.execute("SET LOCAL statement_timeout = '1s'")
                jdbc.jdbcTemplate.execute("SET LOCAL lock_timeout = '250ms'")
                jdbc.queryForObject(
                    """SELECT public.record_operator_ai_gross_usage_v1(:id, :owner, :source, :provider, :estimate)""",
                    mapOf(
                        "id" to reservationId,
                        "owner" to ownerUserId,
                        "source" to source,
                        "provider" to provider,
                        "estimate" to maxGrossMicrousd,
                    ),
                    Boolean::class.java,
                )
            }
        } catch (_: Exception) {
            LOGGER.warn("operator_ai_usage_meter_unavailable provider={} source={}", provider, source)
        }
    }

    private companion object {
        val LOGGER = LoggerFactory.getLogger(OperatorAiUsageMeter::class.java)
    }
}
