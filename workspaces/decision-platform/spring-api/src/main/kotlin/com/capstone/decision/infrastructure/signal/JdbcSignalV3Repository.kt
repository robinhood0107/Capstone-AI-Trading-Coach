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
    private val mapper: tools.jackson.databind.ObjectMapper,
) : SignalV3ProductionReadPort {
    override fun find(symbol: String): SignalV3ReadSnapshot {
        val jdbc = jdbcProvider.getIfAvailable() ?: throw SignalStorageUnavailableException()
        return try {
            // 두 STABLE 함수를 한 statement로 읽어 배치 게시 중에도 같은 DB snapshot을 사용한다.
            val rows =
                jdbc.query(
                    "SELECT signal.*, p1_read_ridge_forecasts_v1(:symbol)::text AS ridge_json " +
                        "FROM p1_read_return_signal_v3(:symbol) signal",
                    mapOf("symbol" to symbol),
                ) { result, _ ->
                    val ridge = result.getString("ridge_json")?.let { mapper.readTree(it) }
                    val forecasts =
                        ridge?.path("forecasts")?.toList()?.map { node ->
                            com.capstone.decision.application.signal.RuntimeReturnForecast(
                                node.path("horizonSessions").intValue(),
                                LocalDate.parse(node.path("targetSession").stringValue()),
                                node.path("expectedReturn").doubleValue(),
                                node.path("forecastClose").doubleValue(),
                                node.path("trainSamples").intValue(),
                                LocalDate.parse(node.path("trainedThrough").stringValue()),
                            )
                        }
                    StoredSignalV3Component(
                        producer = result.getString("producer"),
                        sourceWorkspace = result.getString("source_workspace"),
                        sessionDate = result.getObject("session_date", LocalDate::class.java),
                        asOf = requireNotNull(result.getTimestamp("as_of")).toInstant(),
                        signal = result.getString("signal"),
                        predictedReturn = result.getBigDecimal("predicted_return").toDouble(),
                        modelVersion = result.getString("model_version"),
                        modelReportId = result.getString("model_report_id"),
                        returnForecasts = forecasts,
                        sourceSession = ridge?.path("sourceSession")?.stringValue()?.let { LocalDate.parse(it) },
                        ridgeModelVersion = ridge?.path("modelVersion")?.stringValue(),
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
