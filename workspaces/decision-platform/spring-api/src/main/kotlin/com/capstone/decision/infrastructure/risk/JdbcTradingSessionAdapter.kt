package com.capstone.decision.infrastructure.risk

import com.capstone.decision.domain.risk.TradingSessionBoundary
import com.capstone.decision.domain.risk.TradingSessionPort
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import java.time.LocalDate
import java.time.OffsetDateTime

/**
 * previous-trading-day 판정은 V6 `trading_sessions`만 읽어 두 번째 writable calendar를 만들지 않는다.
 */
@Repository
class JdbcTradingSessionAdapter(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
) : TradingSessionPort {
    override fun previousOpenSessions(
        beforeDate: LocalDate,
        limit: Int,
    ): List<TradingSessionBoundary> {
        require(limit in 1..2)
        return jdbc().query(
            """
            SELECT session_date, close_at
            FROM trading_sessions
            WHERE exchange_mic = 'XKRX'
              AND is_open = true
              AND session_date < :beforeDate
            ORDER BY session_date DESC
            LIMIT :limit
            """.trimIndent(),
            mapOf(
                "beforeDate" to beforeDate,
                "limit" to limit,
            ),
        ) { result, _ ->
            TradingSessionBoundary(
                sessionDate = result.getObject("session_date", LocalDate::class.java),
                closeAt = result.getObject("close_at", OffsetDateTime::class.java).toInstant(),
            )
        }
    }

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable()
            ?: error("Trading-session JDBC access is unavailable without a configured DataSource.")
}
