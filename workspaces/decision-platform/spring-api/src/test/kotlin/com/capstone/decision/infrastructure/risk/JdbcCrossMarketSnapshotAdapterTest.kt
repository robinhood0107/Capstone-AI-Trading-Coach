package com.capstone.decision.infrastructure.risk

import com.capstone.decision.application.risk.crossmarket.CrossMarketRiskSnapshot
import com.capstone.decision.application.risk.crossmarket.CrossMarketSnapshotReadRequest
import com.capstone.decision.application.risk.crossmarket.CrossMarketSnapshotReadResult
import com.capstone.decision.application.risk.crossmarket.CrossMarketSnapshotUnavailableReason
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.sql.PreparedStatement
import java.sql.ResultSet
import java.time.Instant

class JdbcCrossMarketSnapshotAdapterTest {
    private val query = mockk<ActorScopedReadQuery>()
    private val adapter = JdbcCrossMarketSnapshotAdapter(query)

    @Test
    fun `reads only owner scoped bounded latest snapshot without decision wiring`() {
        val sql = slot<String>()
        val binder = slot<(PreparedStatement) -> Unit>()
        every {
            query.query<CrossMarketRiskSnapshot>(
                ACTOR,
                capture(sql),
                null,
                null,
                capture(binder),
                any<(ResultSet) -> CrossMarketRiskSnapshot>(),
            )
        } returns listOf(snapshot())
        val statement = mockk<PreparedStatement>(relaxed = true)

        val result = adapter.load(request())

        assertThat(result).isInstanceOf(CrossMarketSnapshotReadResult.Available::class.java)
        assertThat((result as CrossMarketSnapshotReadResult.Available).snapshot.artifactHash)
            .isEqualTo("4".repeat(64))
        assertThat(sql.captured).contains("FROM latest_cross_market_risk_snapshots")
        assertThat(sql.captured).doesNotContain("FROM cross_market_risk_snapshots")
        assertThat(sql.captured).contains("owner_scope_hash = ?", "config_version = ?", "LIMIT 2")
        binder.captured(statement)
        verify(exactly = 1) { statement.setString(1, OWNER_SCOPE) }
        verify(exactly = 1) { statement.setString(2, CONFIG_VERSION) }
    }

    @Test
    fun `missing duplicate future and unavailable rows remain typed unavailable`() {
        every {
            query.query<CrossMarketRiskSnapshot>(any(), any(), null, null, any(), any())
        } returnsMany
            listOf(
                emptyList(),
                listOf(snapshot(), snapshot(identity = "9".repeat(64))),
                listOf(snapshot(availableAt = EVALUATED_AT.plusSeconds(1))),
                listOf(snapshot(availability = "UNAVAILABLE")),
            )

        assertUnavailable(CrossMarketSnapshotUnavailableReason.MISSING)
        assertUnavailable(CrossMarketSnapshotUnavailableReason.DUPLICATE)
        assertUnavailable(CrossMarketSnapshotUnavailableReason.FUTURE)
        assertUnavailable(CrossMarketSnapshotUnavailableReason.SOURCE_UNAVAILABLE)
    }

    private fun assertUnavailable(expected: CrossMarketSnapshotUnavailableReason) {
        val result = adapter.load(request())
        assertThat(result).isEqualTo(CrossMarketSnapshotReadResult.Unavailable(expected))
    }

    private fun request() =
        CrossMarketSnapshotReadRequest(
            actorUserId = ACTOR,
            ownerScopeHash = OWNER_SCOPE,
            configVersion = CONFIG_VERSION,
            evaluationAsOf = EVALUATED_AT,
        )

    private fun snapshot(
        identity: String = "1".repeat(64),
        availability: String = "AVAILABLE",
        availableAt: Instant = EVALUATED_AT.minusSeconds(1),
    ) = CrossMarketRiskSnapshot(
        logicalIdentityHash = identity,
        ownerScopeHash = OWNER_SCOPE,
        configVersion = CONFIG_VERSION,
        availability = availability,
        evidenceMode = "SYNTHETIC_FIXTURE",
        snapshotAvailableAt = availableAt,
        decisionAuthority = "NEW_BUY_ALLOW_TO_WARN_ONLY",
        orderAuthority = "NONE",
        validationStatus = "UNVALIDATED",
        artifactHash = "4".repeat(64),
        canonicalPayloadJson = "{\"sanitized\":true}",
    )

    private companion object {
        const val ACTOR = "usr_demo_user"
        val OWNER_SCOPE = "2".repeat(64)
        const val CONFIG_VERSION = "cross-market-risk-config.v1"
        val EVALUATED_AT: Instant = Instant.parse("2026-07-31T00:30:00Z")
    }
}
