package com.capstone.decision.application.market

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset

class ForeignNewsSentimentServiceTest {
    @Test
    fun `missing aggregate is an honest four-lane not-activated abstain`() {
        val service =
            ForeignNewsSentimentService(
                readPort = FixedReadPort(null),
                clock = Clock.fixed(Instant.parse("2026-08-09T01:00:00Z"), ZoneOffset.UTC),
            )

        val response = service.read("usr_demo_user", "005930")

        assertThat(response.status).isEqualTo("ABSTAIN")
        assertThat(response.asOf).isEqualTo(Instant.parse("2026-08-09T01:00:00Z"))
        assertThat(response.lanes.map(ForeignNewsLaneState::laneId)).containsExactly(
            "FINNHUB_PERSONAL_LOCAL",
            "SEC_OFFICIAL",
            "FED_OFFICIAL",
            "GDELT_OFFLINE_REFERENCE",
        )
        assertThat(response.lanes.map(ForeignNewsLaneState::state)).containsOnly("NOT_ACTIVATED")
        assertThat(response.decisionAuthority).isEqualTo("NONE")
        assertThat(response.allowedUses).containsExactly("EXPLANATION_ONLY")
        assertThat(response.rawProviderDataStored).isFalse()
        assertThat(response.articleMetadataStored).isFalse()
    }

    @Test
    fun `service returns the persisted sanitized projection without changing authority`() {
        val persisted =
            ForeignNewsSentiment(
                allowedUses = listOf("EXPLANATION_ONLY"),
                articleMetadataStored = false,
                asOf = Instant.parse("2026-08-09T01:00:00Z"),
                contractId = "foreign-news-sentiment-v1",
                decisionAuthority = "NONE",
                lanes =
                    listOf(
                        ForeignNewsLaneState("FINNHUB_PERSONAL_LOCAL", "NOT_ACTIVATED"),
                        ForeignNewsLaneState("SEC_OFFICIAL", "NOT_ACTIVATED"),
                        ForeignNewsLaneState("FED_OFFICIAL", "NOT_ACTIVATED"),
                        ForeignNewsLaneState("GDELT_OFFLINE_REFERENCE", "AVAILABLE"),
                    ),
                rawProviderDataStored = false,
                riskDecisionHashIncluded = false,
                s5FeatureEligible = false,
                schemaVersion = 1,
                status = "AVAILABLE",
                symbol = "005930",
            )
        val service =
            ForeignNewsSentimentService(
                readPort = FixedReadPort(persisted),
            )

        assertThat(service.read("usr_demo_user", "005930")).isSameAs(persisted)
    }

    @Test
    fun `client cannot use a lower-case or unbounded symbol selector`() {
        val service =
            ForeignNewsSentimentService(
                readPort = FixedReadPort(null),
            )

        assertThrows<ForeignNewsSentimentValidationException> {
            service.read("usr_demo_user", "aapl")
        }
    }

    private class FixedReadPort(
        private val response: ForeignNewsSentiment?,
    ) : ForeignNewsSentimentReadPort {
        override fun findLatest(
            ownerUserId: String,
            symbol: String,
        ): ForeignNewsSentiment? = response
    }
}
