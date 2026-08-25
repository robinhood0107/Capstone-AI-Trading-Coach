package com.capstone.decision.application.market

import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional
import java.time.Clock
import java.time.Instant

@Service
class ForeignNewsSentimentService(
    private val readPort: ForeignNewsSentimentReadPort,
    private val clock: Clock = Clock.systemUTC(),
) {
    /**
     * owner-local persisted aggregate만 읽고, 아직 materialization되지 않은 lane은 honest ABSTAIN으로 표현한다.
     * 이 service는 provider transport, Decision/Signal/Risk/order integration을 만들지 않는다.
     */
    @Transactional
    fun read(
        ownerUserId: String,
        symbol: String,
    ): ForeignNewsSentiment {
        validateSymbol(symbol)
        return readPort.findLatest(ownerUserId, symbol) ?: notActivated(symbol, Instant.now(clock))
    }

    private fun notActivated(
        symbol: String,
        now: Instant,
    ): ForeignNewsSentiment =
        ForeignNewsSentiment(
            allowedUses = listOf("EXPLANATION_ONLY"),
            articleMetadataStored = false,
            asOf = now,
            contractId = "foreign-news-sentiment-v1",
            decisionAuthority = "NONE",
            lanes =
                listOf(
                    ForeignNewsLaneState("FINNHUB_PERSONAL_LOCAL", "NOT_ACTIVATED"),
                    ForeignNewsLaneState("SEC_OFFICIAL", "NOT_ACTIVATED"),
                    ForeignNewsLaneState("FED_OFFICIAL", "NOT_ACTIVATED"),
                    ForeignNewsLaneState("GDELT_OFFLINE_REFERENCE", "NOT_ACTIVATED"),
                ),
            rawProviderDataStored = false,
            riskDecisionHashIncluded = false,
            s5FeatureEligible = false,
            schemaVersion = 1,
            status = "ABSTAIN",
            symbol = symbol,
        )

    private fun validateSymbol(symbol: String) {
        if (!SYMBOL.matches(symbol)) {
            throw ForeignNewsSentimentValidationException()
        }
    }

    private companion object {
        val SYMBOL = Regex("^[0-9A-Z._:-]{1,20}$")
    }
}
