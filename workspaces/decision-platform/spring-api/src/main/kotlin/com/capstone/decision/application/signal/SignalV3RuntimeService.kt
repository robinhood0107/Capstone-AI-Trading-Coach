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
    val returnForecasts: List<RuntimeReturnForecast>? = null,
    val sourceSession: LocalDate? = null,
    val ridgeModelVersion: String? = null,
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
        // 한 배치가 31종목을 한꺼번에 만들므로 두 producer 행의 대상 세션은 같다.
        val targetSession =
            snapshot.rows
                .map { it.sessionDate }
                .distinct()
                .singleOrNull()
        val rule = component(byProducer["RULE_BASELINE"], "RULE_BASELINE")
        val lstm = component(byProducer["LSTM"], "LSTM")
        val available = listOf(rule, lstm).filter { it.status == "AVAILABLE" }
        val combined =
            if (available.size ==
                2
            ) {
                (requireNotNull(rule.predictedReturn) + requireNotNull(lstm.predictedReturn)) / 2.0
            } else {
                null
            }
        val newest = available.maxByOrNull { requireNotNull(it.asOf) }
        return RuntimeSignalResponse(
            symbol = symbol,
            asOf = newest?.asOf,
            timeframe = "1d",
            modelReportId = newest?.modelReportId,
            composite =
                if (combined == null) {
                    RuntimeSignalComposite(status = "ABSTAIN", reason = "REQUIRED_COMPONENT_UNAVAILABLE")
                } else {
                    RuntimeSignalComposite(
                        status = "AVAILABLE",
                        predictedReturn = combined,
                        signal =
                            if (combined > 0.005) {
                                "BUY"
                            } else if (combined < -0.005) {
                                "SELL"
                            } else {
                                "HOLD"
                            },
                    )
                },
            components =
                RuntimeSignalComponents(
                    ruleBaseline = rule,
                    lstm = lstm,
                    lightgbm = abstain("LIGHTGBM", "decision-platform", "MISSING_EVIDENCE"),
                    hmmRegime = abstain("HMM", "decision-platform", "MISSING_EVIDENCE"),
                ),
            warnings =
                buildList {
                    add("LightGBM and HMM remain outside current P1 production authority.")
                    // 대상 세션이 지난 예측을 "현재 신호"처럼 보이게 두지 않는다.
                    if (targetSession != null && targetSession < latest) {
                        add("Prediction target session $targetSession has already passed.")
                    }
                },
        )
    }

    private fun component(
        row: StoredSignalV3Component?,
        producer: String,
    ): RuntimeSignalComponent {
        if (row == null) return abstain(producer, "return-engine", "MISSING_EVIDENCE")
        // 예측 대상 세션이 지난 것은 "근거가 없다"가 아니다. 그 예측은 실제로 만들어졌고
        // 그 날짜에 대해 유효했다 - 소진됐을 뿐이다. 예전에는 여기서 STALE_EVIDENCE 로
        // ABSTAIN 했고, `latest` 가 "아직 마감되지 않은 가장 이른 세션"이라(V134 의
        // latest_completed_session 은 이름과 의미가 어긋나 있다) 장 마감부터 다음 아침
        // 배치까지 모델 비교 화면이 통째로 비었다. 이 endpoint 의 소비자는 대시보드
        // 하나뿐이고 주문 경로는 p1_read_automation_runtime_state_v4 를 쓰므로,
        // 값을 숨기는 대신 대상 세션이 지났다는 사실을 warnings 로 알린다.
        // reason enum 은 contracts/schemas 에 고정돼 있어 새 코드를 만들 수 없다.
        if (
            row.sourceWorkspace != "return-engine" ||
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
            modelVersion = if (producer == "RULE_BASELINE") row.ridgeModelVersion ?: row.modelVersion else row.modelVersion,
            returnForecasts = if (producer == "RULE_BASELINE") row.returnForecasts else null,
            estimator = if (producer == "RULE_BASELINE" && row.returnForecasts != null) "RIDGE" else null,
            sourceSession = row.sourceSession,
            qualityStatus = if (row.returnForecasts != null) "COMPARISON_PENDING" else null,
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
