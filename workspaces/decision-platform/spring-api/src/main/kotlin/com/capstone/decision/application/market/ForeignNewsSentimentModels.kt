package com.capstone.decision.application.market

import java.time.Instant

/** foreign-news route가 허용하는 네 lane의 API-safe 상태다. */
data class ForeignNewsLaneState(
    val laneId: String,
    val state: String,
)

/**
 * 이 projection은 explanation-only aggregate만 노출한다.
 * 기사 metadata/body, provider raw response, credential, request query/header는 어떤 field에도 포함하지 않는다.
 */
data class ForeignNewsSentiment(
    val allowedUses: List<String>,
    val articleMetadataStored: Boolean,
    val asOf: Instant,
    val contractId: String,
    val decisionAuthority: String,
    val lanes: List<ForeignNewsLaneState>,
    val rawProviderDataStored: Boolean,
    val riskDecisionHashIncluded: Boolean,
    val s5FeatureEligible: Boolean,
    val schemaVersion: Int,
    val status: String,
    val symbol: String,
)

/** DB가 없거나 malformed sanitized projection을 반환하면 route는 fabricated provider state 없이 503으로 닫는다. */
class ForeignNewsSentimentUnavailableException : RuntimeException()

/** path symbol 또는 DB payload가 foreign-news contract shape를 벗어났음을 나타낸다. */
class ForeignNewsSentimentValidationException : RuntimeException()

/** owner actor와 symbol을 bind한 latest sanitized aggregate read port다. */
interface ForeignNewsSentimentReadPort {
    fun findLatest(
        ownerUserId: String,
        symbol: String,
    ): ForeignNewsSentiment?
}
