package com.capstone.decision.domain.risk

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.Arguments
import org.junit.jupiter.params.provider.MethodSource
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.util.stream.Stream

class SourceFreshnessPolicyTest {
    @ParameterizedTest(name = "{0}")
    @MethodSource("fixedTtls")
    fun `every fixed TTL is fresh at exact max age and stale one nanosecond later`(
        name: String,
        ttl: Duration,
    ) {
        val observedAt = Instant.parse("2030-01-02T03:04:05Z")
        val policy = SourceFreshnessPolicy()

        assertThat(
            policy.fixedTtl(observedAt, observedAt.plus(ttl).minusNanos(1), ttl).state,
        ).describedAs("$name boundary-1").isEqualTo(FreshnessState.FRESH)
        assertThat(
            policy.fixedTtl(observedAt, observedAt.plus(ttl), ttl).state,
        ).describedAs("$name exact").isEqualTo(FreshnessState.FRESH)
        assertThat(
            policy.fixedTtl(observedAt, observedAt.plus(ttl).plusNanos(1), ttl).state,
        ).describedAs("$name boundary+1").isEqualTo(FreshnessState.STALE)
    }

    @Test
    fun `future timestamp is never fresh`() {
        val now = Instant.parse("2030-01-02T03:04:05Z")

        val assessment =
            SourceFreshnessPolicy().fixedTtl(
                observedAt = now.plusNanos(1),
                evaluationAsOf = now,
                maxAge = Duration.ofSeconds(300),
            )

        assertThat(assessment.state).isEqualTo(FreshnessState.FUTURE)
    }

    @Test
    fun `Friday to Monday holiday chain and year boundary use one previous trading day function`() {
        val sessions =
            listOf(
                session("2029-12-28", "2029-12-28T06:30:00Z"),
                session("2030-01-04", "2030-01-04T06:30:00Z"),
                session("2030-01-11", "2030-01-11T06:30:00Z"),
            )
        val policy = PreviousTradingDayFreshnessPolicy(FakeTradingSessionPort(sessions))

        assertThat(
            policy
                .assess(
                    Instant.parse("2029-12-28T06:30:00Z"),
                    Instant.parse("2030-01-02T00:00:00Z"),
                ).previousSessionDate,
        ).isEqualTo(LocalDate.parse("2029-12-28"))
        assertThat(
            policy
                .assess(
                    Instant.parse("2030-01-04T06:30:00Z"),
                    Instant.parse("2030-01-07T00:00:00Z"),
                ).previousSessionDate,
        ).isEqualTo(LocalDate.parse("2030-01-04"))
        assertThat(
            policy
                .assess(
                    Instant.parse("2030-01-11T06:30:00Z"),
                    Instant.parse("2030-01-14T00:00:00Z"),
                ).previousSessionDate,
        ).isEqualTo(LocalDate.parse("2030-01-11"))
    }

    @Test
    fun `calendar policy has no intraday or after-hours branch`() {
        val friday = session("2030-01-04", "2030-01-04T06:30:00Z")
        val policy = PreviousTradingDayFreshnessPolicy(FakeTradingSessionPort(listOf(friday)))

        val morning =
            policy.assess(
                friday.closeAt,
                Instant.parse("2030-01-07T00:01:00Z"),
            )
        val evening =
            policy.assess(
                friday.closeAt,
                Instant.parse("2030-01-07T14:59:00Z"),
            )

        assertThat(morning.previousSessionDate).isEqualTo(evening.previousSessionDate)
        assertThat(morning.state).isEqualTo(FreshnessState.FRESH)
        assertThat(evening.state).isEqualTo(FreshnessState.FRESH)
    }

    @Test
    fun `missing or duplicate previous session fails closed`() {
        val now = Instant.parse("2030-01-07T00:00:00Z")
        val duplicate =
            listOf(
                session("2030-01-04", "2030-01-04T06:30:00Z"),
                session("2030-01-04", "2030-01-04T06:31:00Z"),
            )

        assertThrows<TradingCalendarUnavailableException> {
            PreviousTradingDayFreshnessPolicy(FakeTradingSessionPort(emptyList()))
                .assess(now.minusSeconds(1), now)
        }
        assertThrows<TradingCalendarUnavailableException> {
            PreviousTradingDayFreshnessPolicy(FakeTradingSessionPort(duplicate))
                .assess(now.minusSeconds(1), now)
        }
    }

    private class FakeTradingSessionPort(
        private val sessions: List<TradingSessionBoundary>,
    ) : TradingSessionPort {
        override fun previousOpenSessions(
            beforeDate: LocalDate,
            limit: Int,
        ): List<TradingSessionBoundary> =
            sessions
                .filter { it.sessionDate < beforeDate }
                .sortedByDescending(TradingSessionBoundary::sessionDate)
                .take(limit)
    }

    companion object {
        @JvmStatic
        fun fixedTtls(): Stream<Arguments> =
            Stream.of(
                Arguments.of("price/orderbook", Duration.ofSeconds(300)),
                Arguments.of("balance", Duration.ofSeconds(60)),
                Arguments.of("60m signal", Duration.ofMinutes(90)),
                Arguments.of("news", Duration.ofHours(24)),
                Arguments.of("disclosure", Duration.ofHours(24)),
                Arguments.of("ECOS", Duration.ofDays(7)),
            )

        private fun session(
            date: String,
            closeAt: String,
        ): TradingSessionBoundary =
            TradingSessionBoundary(
                sessionDate = LocalDate.parse(date),
                closeAt = Instant.parse(closeAt),
            )
    }
}
