package com.capstone.decision.application.risk.port

import com.capstone.decision.domain.risk.EvaluationBounds
import com.capstone.decision.domain.risk.PortfolioSource

data class PortfolioContextRef(
    val opaqueRef: String,
    val source: PortfolioSource,
    val ownerScopeHash: String,
) {
    init {
        require(opaqueRef.isNotBlank() && opaqueRef.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(OWNER_SCOPE_HASH.matches(ownerScopeHash))
    }

    private companion object {
        val OWNER_SCOPE_HASH = Regex(EvaluationBounds.SANITIZED_SHA256_PATTERN)
    }
}

sealed interface PortfolioContextResolution {
    data class Available(
        val context: PortfolioContextRef,
    ) : PortfolioContextResolution

    data class Unavailable(
        val reason: PortfolioContextUnavailableReason,
    ) : PortfolioContextResolution
}

enum class PortfolioContextUnavailableReason {
    MISSING,
    CONFLICT,
}

// raw account ID를 받지 않고 actor와 명시 source로 서버 소유 context만 해석한다.
interface PortfolioContextPort {
    fun resolve(
        actorUserId: String,
        source: PortfolioSource,
    ): PortfolioContextResolution
}
