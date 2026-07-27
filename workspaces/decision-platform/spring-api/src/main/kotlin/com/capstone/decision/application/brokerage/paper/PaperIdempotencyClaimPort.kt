package com.capstone.decision.application.brokerage.paper

data class PaperIdempotencyClaim(
    val scopeHash: String,
    val requestHash: String,
    val token: String,
)

sealed interface PaperClaimLookup {
    data class Acquired(
        val claim: PaperIdempotencyClaim,
    ) : PaperClaimLookup

    data object Conflict : PaperClaimLookup

    data object InProgress : PaperClaimLookup
}

/**
 * 진행 중 paper 요청만 짧게 직렬화하며 완료 응답의 진실 소스는 PostgreSQL orders row다.
 * 구현체는 HMAC scope 외의 사용자 식별자나 raw idempotency key를 저장하면 안 된다.
 */
interface PaperIdempotencyClaimPort {
    fun acquire(
        scopeHash: String,
        requestHash: String,
    ): PaperClaimLookup

    fun release(claim: PaperIdempotencyClaim)
}

class PaperIdempotencyInProgressException : RuntimeException("Paper order request is already in progress.")
