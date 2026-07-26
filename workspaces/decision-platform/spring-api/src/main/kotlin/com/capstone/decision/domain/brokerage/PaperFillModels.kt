package com.capstone.decision.domain.brokerage

import java.time.Instant

enum class PaperPriceBasis {
    LAST_QUOTE,
    PREVIOUS_CLOSE,
}

enum class PaperFeeModel {
    NONE_V1,
}

enum class PaperAcceptedReason {
    LIMIT_NOT_FILLED,
}

enum class PaperFillFailure {
    INVALID_INPUT,
    PRICE_UNAVAILABLE,
    ARITHMETIC_OVERFLOW,
}

data class PaperPriceObservation(
    val observationId: String,
    val lastPriceKrw: Long?,
    val previousCloseKrw: Long?,
    val completeness: String,
    val observedAt: Instant,
)

data class PaperFillRequest(
    val side: String,
    val orderType: String,
    val quantity: Long,
    val limitPriceKrw: Long?,
)

sealed interface PaperFillDecision {
    data class Filled(
        val quantity: Long,
        val priceKrw: Long,
        val amountKrw: Long,
        val priceBasis: PaperPriceBasis,
        val slippageBps: Int,
        val feeModel: PaperFeeModel,
        val observedAt: Instant,
    ) : PaperFillDecision

    data class Accepted(
        val reason: PaperAcceptedReason,
        val fill: Nothing? = null,
    ) : PaperFillDecision
}

class PaperFillPolicyException(
    val failure: PaperFillFailure,
    cause: Throwable? = null,
) : RuntimeException("Paper fill policy rejected the input.", cause)

enum class PaperLedgerFailure {
    INVALID_STATE,
    INSUFFICIENT_CASH,
    INSUFFICIENT_POSITION,
    ARITHMETIC_OVERFLOW,
}

data class PaperLedgerState(
    val cashKrw: Long,
    val quantity: Long,
    val averagePriceKrw: Long,
    val marketValueKrw: Long,
)

data class PaperLedgerMutation(
    val before: PaperLedgerState,
    val after: PaperLedgerState,
    val fillAmountKrw: Long,
)

class PaperLedgerException(
    val failure: PaperLedgerFailure,
    cause: Throwable? = null,
) : RuntimeException("Paper ledger policy rejected the mutation.", cause)
