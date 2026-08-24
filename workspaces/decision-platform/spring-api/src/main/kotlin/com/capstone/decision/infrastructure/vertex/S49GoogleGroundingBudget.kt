package com.capstone.decision.infrastructure.vertex

import com.capstone.decision.application.rag.RagV2VertexEvidence
import com.capstone.decision.application.rag.StrongLlmAnswerBasis
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component
import org.springframework.transaction.annotation.Transactional
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Clock
import java.time.LocalDate
import java.time.ZoneId
import java.util.UUID

@ConfigurationProperties("app.rag-v2.web.vertex-google-search")
data class S49GoogleGroundingProperties(
    var enabled: Boolean = true,
    var overageAllowed: Boolean = false,
    var monthlySoftCap: Int = 4_000,
    var reservePerPrompt: Int = 8,
    var billingPeriodZone: String = "America/Los_Angeles",
    var billingAccountFingerprint: String = "",
) {
    fun validate() {
        require(!overageAllowed)
        require(monthlySoftCap in 1..5_000)
        require(reservePerPrompt in 1..8)
        require(billingPeriodZone == "America/Los_Angeles")
        if (enabled) require(SHA256.matches(billingAccountFingerprint))
    }

    private companion object {
        val SHA256 = Regex("^[0-9a-f]{64}$")
    }
}

data class S49GoogleBudgetPermit(
    val googleEnabled: Boolean,
    val reservationId: String?,
)

internal interface S49GoogleGroundingBudgetPort {
    fun reserve(
        ownerUserId: String,
        requestId: String,
    ): S49GoogleBudgetPermit

    fun commit(
        ownerUserId: String,
        reservationId: String,
        actualQueryCount: Int,
    )

    fun unknown(
        ownerUserId: String,
        reservationId: String,
    )

    fun release(
        ownerUserId: String,
        reservationId: String,
    )
}

/** Pacific billing month의 local observed+reserved+unknown 합계를 DB row lock으로 원자 제한한다. */
@Component
@ConditionalOnProperty(name = ["app.s4-9.strong-llm.enabled"], havingValue = "true")
internal class JdbcS49GoogleGroundingBudget(
    private val jdbcTemplate: JdbcTemplate,
    private val properties: S49GoogleGroundingProperties,
    private val clock: Clock = Clock.systemUTC(),
) : S49GoogleGroundingBudgetPort {
    init {
        properties.validate()
    }

    @Transactional
    override fun reserve(
        ownerUserId: String,
        requestId: String,
    ): S49GoogleBudgetPermit {
        if (!properties.enabled) return S49GoogleBudgetPermit(false, null)
        setActor(ownerUserId)
        val reservationId = "s49_gbr_${UUID.randomUUID().toString().replace("-", "")}"
        val period = s49GoogleBillingPeriodStart(clock, properties.billingPeriodZone)
        val accepted =
            jdbcTemplate.queryForObject(
                "SELECT public.reserve_s4_9_google_grounding_budget(?,?,?,?,?,?,?)",
                Boolean::class.java,
                reservationId,
                ownerUserId,
                requestId,
                properties.billingAccountFingerprint,
                period,
                properties.reservePerPrompt,
                properties.monthlySoftCap,
            ) == true
        return S49GoogleBudgetPermit(accepted, reservationId.takeIf { accepted })
    }

    @Transactional
    override fun commit(
        ownerUserId: String,
        reservationId: String,
        actualQueryCount: Int,
    ) = settle(ownerUserId, reservationId, "COMMITTED", actualQueryCount)

    @Transactional
    override fun unknown(
        ownerUserId: String,
        reservationId: String,
    ) = settle(ownerUserId, reservationId, "UNKNOWN_BILLING", null)

    @Transactional
    override fun release(
        ownerUserId: String,
        reservationId: String,
    ) = settle(ownerUserId, reservationId, "RELEASED", null)

    private fun settle(
        ownerUserId: String,
        reservationId: String,
        outcome: String,
        actual: Int?,
    ) {
        setActor(ownerUserId)
        jdbcTemplate.queryForObject(
            "SELECT public.settle_s4_9_google_grounding_budget(?,?,?,?) IS NULL",
            Boolean::class.java,
            ownerUserId,
            reservationId,
            outcome,
            actual,
        )
    }

    private fun setActor(ownerUserId: String) {
        jdbcTemplate.queryForObject("SELECT set_config('app.actor_user_id', ?, true)", String::class.java, ownerUserId)
    }
}

internal fun s49GoogleBillingPeriodStart(
    clock: Clock,
    billingPeriodZone: String,
): LocalDate = LocalDate.ofInstant(clock.instant(), ZoneId.of(billingPeriodZone)).withDayOfMonth(1)

internal data class S49StrongLlmUsageV2(
    val promptTokens: Int,
    val outputTokens: Int,
    val toolRounds: Int,
    val searchCalls: Int,
    val readCalls: Int,
    val vertexGenerateCalls: Int,
    val googleGroundingQueries: Int,
    val searchBackend: String,
    val evidenceValidationMode: String,
)

internal interface S49StrongLlmUsageV2Port {
    fun commit(
        ownerUserId: String,
        requestId: String,
        modelId: String,
        basis: StrongLlmAnswerBasis,
        evidence: List<RagV2VertexEvidence>,
        usage: S49StrongLlmUsageV2,
    )

    fun failed(
        ownerUserId: String,
        requestId: String,
        modelId: String,
        evidence: List<RagV2VertexEvidence>,
        usage: S49StrongLlmUsageV2,
        failureLeaf: String,
        unknownBilling: Boolean,
    )
}

internal interface S49StrongLlmCompletionPort {
    fun commit(
        ownerUserId: String,
        reservationId: String?,
        actualGoogleQueryCount: Int,
        requestId: String,
        modelId: String,
        basis: StrongLlmAnswerBasis,
        evidence: List<RagV2VertexEvidence>,
        usage: S49StrongLlmUsageV2,
    )
}

/** Google query 정산과 성공 usage ledger가 서로 다른 결과로 남지 않게 한 DB transaction으로 완료한다. */
@Component
@ConditionalOnProperty(name = ["app.s4-9.strong-llm.enabled"], havingValue = "true")
internal class TransactionalS49StrongLlmCompletion(
    private val googleBudget: S49GoogleGroundingBudgetPort,
    private val usageLedger: S49StrongLlmUsageV2Port,
) : S49StrongLlmCompletionPort {
    @Transactional
    override fun commit(
        ownerUserId: String,
        reservationId: String?,
        actualGoogleQueryCount: Int,
        requestId: String,
        modelId: String,
        basis: StrongLlmAnswerBasis,
        evidence: List<RagV2VertexEvidence>,
        usage: S49StrongLlmUsageV2,
    ) {
        reservationId?.let { googleBudget.commit(ownerUserId, it, actualGoogleQueryCount) }
        usageLedger.commit(ownerUserId, requestId, modelId, basis, evidence, usage)
    }
}

@Component
@ConditionalOnProperty(name = ["app.s4-9.strong-llm.enabled"], havingValue = "true")
internal class JdbcS49StrongLlmUsageV2Ledger(
    private val jdbcTemplate: JdbcTemplate,
) : S49StrongLlmUsageV2Port {
    @Transactional
    override fun commit(
        ownerUserId: String,
        requestId: String,
        modelId: String,
        basis: StrongLlmAnswerBasis,
        evidence: List<RagV2VertexEvidence>,
        usage: S49StrongLlmUsageV2,
    ) = record(ownerUserId, requestId, modelId, basis.name, "COMMITTED", evidence, usage, null)

    @Transactional
    override fun failed(
        ownerUserId: String,
        requestId: String,
        modelId: String,
        evidence: List<RagV2VertexEvidence>,
        usage: S49StrongLlmUsageV2,
        failureLeaf: String,
        unknownBilling: Boolean,
    ) = record(
        ownerUserId,
        requestId,
        modelId,
        null,
        if (unknownBilling) "UNKNOWN_BILLING" else "REJECTED",
        evidence,
        usage.copy(promptTokens = 0, outputTokens = 0),
        failureLeaf,
    )

    private fun record(
        ownerUserId: String,
        requestId: String,
        modelId: String,
        basis: String?,
        outcome: String,
        evidence: List<RagV2VertexEvidence>,
        usage: S49StrongLlmUsageV2,
        failureLeaf: String?,
    ) {
        jdbcTemplate.queryForObject("SELECT set_config('app.actor_user_id', ?, true)", String::class.java, ownerUserId)
        val evidenceHash = sha256(evidence.joinToString("\n") { "${it.citationId}:${it.canonicalTextSha256}" })
        val eventId = "s49_llu_${sha256("$requestId:v2:$outcome:$evidenceHash").take(32)}"
        jdbcTemplate.queryForObject(
            """
            SELECT public.record_s4_9_strong_llm_usage_v2(
              ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            ) IS NULL
            """.trimIndent(),
            Boolean::class.java,
            eventId,
            ownerUserId,
            requestId,
            modelId,
            basis,
            outcome,
            usage.toolRounds,
            usage.searchCalls,
            usage.readCalls,
            usage.promptTokens.takeIf { outcome == "COMMITTED" },
            usage.outputTokens.takeIf { outcome == "COMMITTED" },
            evidenceHash,
            usage.vertexGenerateCalls,
            usage.googleGroundingQueries,
            usage.searchBackend,
            usage.evidenceValidationMode,
            failureLeaf,
        )
    }

    private fun sha256(value: String): String {
        val bytes = value.toByteArray(StandardCharsets.UTF_8)
        return try {
            MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(java.util.Locale.ROOT, it) }
        } finally {
            bytes.fill(0)
        }
    }
}
