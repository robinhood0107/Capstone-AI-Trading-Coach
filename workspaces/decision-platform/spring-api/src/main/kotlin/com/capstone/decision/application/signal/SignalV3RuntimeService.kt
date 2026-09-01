package com.capstone.decision.application.signal

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ErrorCode
import org.springframework.stereotype.Service
import java.time.Instant
import java.time.LocalDate

data class StoredSignalV3Component(
    val producer: String,
    val sourceWorkspace: String,
    val sessionDate: LocalDate,
    val asOf: Instant,
    val signal: String,
    val predictedReturn: Double,
    val modelVersion: String,
    val modelReportId: String,
)

data class SignalV3ReadSnapshot(
    val rows: List<StoredSignalV3Component>,
    val latestCompletedSession: LocalDate?,
)

fun interface SignalV3ProductionReadPort {
    fun find(symbol: String): SignalV3ReadSnapshot
}

@Service
class SignalV3RuntimeService(
    private val readPort: SignalV3ProductionReadPort,
) {
    fun read(symbol: String): RuntimeSignalResponse {
        if (!SYMBOL.matches(symbol)) {
            throw ApiException(ErrorCode.VALIDATION_ERROR, details = mapOf("symbol" to "Invalid symbol."))
        }
        val snapshot =
            try {
                readPort.find(symbol)
            } catch (error: SignalStorageUnavailableException) {
                throw ApiException(ErrorCode.SIGNAL_UNAVAILABLE).apply { initCause(error) }
            }
        if (snapshot.rows.isEmpty()) return allAbstain(symbol)
        val latest = snapshot.latestCompletedSession ?: throw ApiException(ErrorCode.SIGNAL_UNAVAILABLE)
        val byProducer = snapshot.rows.associateBy { it.producer }
        if (byProducer.size != snapshot.rows.size || byProducer.keys.any { it !in REQUIRED_PRODUCERS }) {
            throw ApiException(ErrorCode.SIGNAL_UNAVAILABLE)
        }
        val rule = component(byProducer["RULE_BASELINE"], "RULE_BASELINE", latest)
        val lstm = component(byProducer["LSTM"], "LSTM", latest)
        val available = listOf(rule, lstm).filter { it.status == "AVAILABLE" }
        val newest = available.maxByOrNull { requireNotNull(it.asOf) }
        return RuntimeSignalResponse(
            symbol = symbol,
            asOf = newest?.asOf,
            timeframe = "1d",
            modelReportId = newest?.modelReportId,
            composite = RuntimeSignalComposite(status = "ABSTAIN", reason = "REQUIRED_COMPONENT_UNAVAILABLE"),
            components =
                RuntimeSignalComponents(
                    ruleBaseline = rule,
                    lstm = lstm,
                    lightgbm = abstain("LIGHTGBM", "decision-platform", "MISSING_EVIDENCE"),
                    hmmRegime = abstain("HMM", "decision-platform", "MISSING_EVIDENCE"),
                ),
            warnings = listOf("LightGBM and HMM remain outside current P1 production authority."),
        )
    }

    private fun component(
        row: StoredSignalV3Component?,
        producer: String,
        latest: LocalDate,
    ): RuntimeSignalComponent {
        if (row == null) return abstain(producer, "return-engine", "MISSING_EVIDENCE")
        if (
            row.sourceWorkspace != "return-engine" ||
            row.sessionDate != latest ||
            row.signal !in SIGNALS ||
            !row.predictedReturn.isFinite() ||
            row.modelVersion.isEmpty() ||
            row.modelVersion.length > 128 ||
            row.modelReportId.isEmpty() ||
            row.modelReportId.length > 128
        ) {
            return abstain(producer, "return-engine", "UNIDENTIFIABLE_OUTPUT")
        }
        return RuntimeSignalComponent(
            status = "AVAILABLE",
            producer = producer,
            sourceWorkspace = "return-engine",
            asOf = row.asOf,
            signal = row.signal,
            predictedReturn = row.predictedReturn,
            modelVersion = row.modelVersion,
            modelReportId = row.modelReportId,
        )
    }

    private fun allAbstain(symbol: String): RuntimeSignalResponse =
        RuntimeSignalResponse(
            symbol = symbol,
            asOf = null,
            timeframe = "1d",
            modelReportId = null,
            composite = RuntimeSignalComposite(status = "ABSTAIN", reason = "REQUIRED_COMPONENT_UNAVAILABLE"),
            components =
                RuntimeSignalComponents(
                    ruleBaseline = abstain("RULE_BASELINE", "return-engine", "MISSING_EVIDENCE"),
                    lstm = abstain("LSTM", "return-engine", "MISSING_EVIDENCE"),
                    lightgbm = abstain("LIGHTGBM", "decision-platform", "MISSING_EVIDENCE"),
                    hmmRegime = abstain("HMM", "decision-platform", "MISSING_EVIDENCE"),
                ),
            warnings = listOf("No verified current daily Signal evidence is available."),
        )

    private fun abstain(
        producer: String,
        workspace: String,
        reason: String,
    ) = RuntimeSignalComponent(
        status = "ABSTAIN",
        producer = producer,
        sourceWorkspace = workspace,
        reason = reason,
    )

    private companion object {
        val SYMBOL = Regex("^[0-9A-Z._:-]{1,20}$")
        val REQUIRED_PRODUCERS = setOf("RULE_BASELINE", "LSTM")
        val SIGNALS = setOf("BUY", "HOLD", "SELL")
    }
}
