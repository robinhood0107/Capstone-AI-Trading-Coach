package com.capstone.decision.domain.risk

import java.time.DateTimeException
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

enum class FreshnessState {
    FRESH,
    STALE,
    FUTURE,
}

data class FreshnessAssessment(
    val state: FreshnessState,
    val freshUntil: Instant,
)

data class PreviousTradingDayAssessment(
    val state: FreshnessState,
    val freshUntil: Instant,
    val previousSessionDate: LocalDate,
)

data class TradingSessionBoundary(
    val sessionDate: LocalDate,
    val closeAt: Instant,
)

// V6 trading_sessions만 읽어 별도의 휴장일 SSOT를 만들지 않는다.
interface TradingSessionPort {
    fun previousOpenSessions(
        beforeDate: LocalDate,
        limit: Int = 2,
    ): List<TradingSessionBoundary>
}

class TradingCalendarUnavailableException : IllegalStateException("Canonical previous trading session is unavailable.")

class SourceFreshnessPolicy {
    /**
     * exact maxAge는 fresh이고 미래 시각은 stale보다 강한 FUTURE로 분리한다.
     */
    fun fixedTtl(
        observedAt: Instant,
        evaluationAsOf: Instant,
        maxAge: Duration,
    ): FreshnessAssessment {
        require(!maxAge.isZero && !maxAge.isNegative) {
            "Freshness maxAge must be positive."
        }
        val freshUntil =
            try {
                observedAt.plus(maxAge)
            } catch (_: DateTimeException) {
                throw IllegalArgumentException("Freshness boundary exceeds Instant range.")
            } catch (_: ArithmeticException) {
                throw IllegalArgumentException("Freshness boundary exceeds Instant range.")
            }
        val state =
            when {
                observedAt.isAfter(evaluationAsOf) -> FreshnessState.FUTURE
                evaluationAsOf.isAfter(freshUntil) -> FreshnessState.STALE
                else -> FreshnessState.FRESH
            }
        return FreshnessAssessment(state, freshUntil)
    }
}

class PreviousTradingDayFreshnessPolicy(
    private val tradingSessionPort: TradingSessionPort,
) {
    /**
     * Asia/Seoul 날짜 하나로 previous session을 정하고 장중/장후 별도 분기를 만들지 않는다.
     */
    fun assess(
        observedAt: Instant,
        evaluationAsOf: Instant,
    ): PreviousTradingDayAssessment {
        val evaluationDate = evaluationAsOf.atZone(SEOUL).toLocalDate()
        val candidates = tradingSessionPort.previousOpenSessions(evaluationDate, limit = 2)
        val previousDate =
            candidates.maxOfOrNull(TradingSessionBoundary::sessionDate)
                ?: throw TradingCalendarUnavailableException()
        val matching = candidates.filter { it.sessionDate == previousDate }
        if (matching.size != 1) {
            throw TradingCalendarUnavailableException()
        }
        val previous = matching.single()
        val nextCalendarBoundary =
            evaluationDate
                .plusDays(1)
                .atStartOfDay(SEOUL)
                .toInstant()
        val state =
            when {
                observedAt.isAfter(evaluationAsOf) -> FreshnessState.FUTURE
                observedAt.isBefore(previous.closeAt) -> FreshnessState.STALE
                else -> FreshnessState.FRESH
            }
        return PreviousTradingDayAssessment(
            state = state,
            freshUntil = nextCalendarBoundary,
            previousSessionDate = previous.sessionDate,
        )
    }

    private companion object {
        val SEOUL: ZoneId = ZoneId.of("Asia/Seoul")
    }
}
