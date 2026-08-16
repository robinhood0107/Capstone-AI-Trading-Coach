package com.capstone.decision.infrastructure.signal

import com.capstone.decision.application.signal.SignalReadSnapshot
import com.capstone.decision.application.signal.SignalStorageUnavailableException
import com.capstone.decision.application.signal.SignalV2ProductionReadPort
import com.capstone.decision.application.signal.StoredSignalComponent
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import java.time.LocalDate

@Repository
class JdbcSignalV2Repository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
) : SignalV2ProductionReadPort {
    /** production pointer와 fixture=false를 DB 함수가 강제한 bounded projection만 읽는다. */
    override fun find(symbol: String): SignalReadSnapshot {
        val jdbc = jdbcProvider.getIfAvailable() ?: throw SignalStorageUnavailableException()
        return try {
            val rows =
                jdbc.query(
                    "SELECT * FROM read_production_signal_v2(:symbol)",
                    mapOf("symbol" to symbol),
                ) { result, _ ->
                    StoredSignalComponent(
                        producer = result.getString("producer"),
                        sourceWorkspace = result.getString("source_workspace"),
                        sessionDate = result.getObject("session_date", LocalDate::class.java),
                        asOf = result.getTimestamp("as_of")?.toInstant(),
                        status = result.getString("status"),
                        reason = result.getString("reason"),
                        signal = result.getString("signal"),
                        confidence = result.getBigDecimal("confidence")?.toDouble(),
                        predictedReturn = result.getBigDecimal("predicted_return")?.toDouble(),
                        modelVersion = result.getString("model_version"),
                        modelReportId = result.getString("model_report_id"),
                    )
                }
            val latest =
                if (rows.isEmpty()) {
                    null
                } else {
                    jdbc.queryForObject(
                        """
                        SELECT max(session_date)
                        FROM trading_sessions
                        WHERE exchange_mic = 'XKRX'
                          AND is_open
                          AND close_at <= (
                            ((clock_timestamp() AT TIME ZONE 'Asia/Seoul')::date + time '08:10')
                            AT TIME ZONE 'Asia/Seoul'
                          )
                        """.trimIndent(),
                        emptyMap<String, Any>(),
                        LocalDate::class.java,
                    )
                }
            SignalReadSnapshot(rows, latest)
        } catch (error: Exception) {
            throw SignalStorageUnavailableException()
        }
    }
}
