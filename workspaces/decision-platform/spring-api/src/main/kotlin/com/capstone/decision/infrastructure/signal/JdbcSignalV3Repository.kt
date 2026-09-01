package com.capstone.decision.infrastructure.signal

import com.capstone.decision.application.signal.SignalStorageUnavailableException
import com.capstone.decision.application.signal.SignalV3ProductionReadPort
import com.capstone.decision.application.signal.SignalV3ReadSnapshot
import com.capstone.decision.application.signal.StoredSignalV3Component
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import java.time.LocalDate

@Repository
class JdbcSignalV3Repository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
) : SignalV3ProductionReadPort {
    override fun find(symbol: String): SignalV3ReadSnapshot {
        val jdbc = jdbcProvider.getIfAvailable() ?: throw SignalStorageUnavailableException()
        return try {
            val rows =
                jdbc.query(
                    "SELECT * FROM p1_read_return_signal_v3(:symbol)",
                    mapOf("symbol" to symbol),
                ) { result, _ ->
                    StoredSignalV3Component(
                        producer = result.getString("producer"),
                        sourceWorkspace = result.getString("source_workspace"),
                        sessionDate = result.getObject("session_date", LocalDate::class.java),
                        asOf = requireNotNull(result.getTimestamp("as_of")).toInstant(),
                        signal = result.getString("signal"),
                        predictedReturn = result.getBigDecimal("predicted_return").toDouble(),
                        modelVersion = result.getString("model_version"),
                        modelReportId = result.getString("model_report_id"),
                    ) to result.getObject("latest_completed_session", LocalDate::class.java)
                }
            val clocks = rows.map { it.second }.toSet()
            if (clocks.size > 1) throw SignalStorageUnavailableException()
            SignalV3ReadSnapshot(rows.map { it.first }, clocks.singleOrNull())
        } catch (error: Exception) {
            throw SignalStorageUnavailableException()
        }
    }
}
