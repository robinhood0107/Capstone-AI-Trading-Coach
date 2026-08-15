package com.capstone.decision.application.signal

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ErrorCode
import org.springframework.stereotype.Service
import java.time.Instant
import java.time.LocalDate

/** DB production reader가 반환하는 검증 완료 component projection이다. */
data class StoredSignalComponent(
    val producer: String,
    val sourceWorkspace: String,
    val sessionDate: LocalDate,
    val asOf: Instant?,
    val status: String,
    val reason: String?,
    val signal: String?,
    val confidence: Double?,
    val predictedReturn: Double?,
    val modelVersion: String?,
    val modelReportId: String?,
)

/** production rows와 stale 판정용 최신 완료 XKRX session을 원자 snapshot으로 전달한다. */
data class SignalReadSnapshot(
    val rows: List<StoredSignalComponent>,
    val latestCompletedSession: LocalDate?,
)

/** Signal v2 API가 fixture를 우회하지 않고 production-only DB function만 호출하는 read port다. */
fun interface SignalV2ProductionReadPort {
    fun find(symbol: String): SignalReadSnapshot
}

/** DB 자체가 불가해 evidence 상태를 판정할 수 없을 때만 typed 503으로 변환한다. */
class SignalStorageUnavailableException : RuntimeException()

/** public component union의 application projection이며 nullable 값은 status 규칙으로 검증된다. */
data class RuntimeSignalComponent(
    val status: String,
    val producer: String,
    val sourceWorkspace: String,
    val asOf: Instant? = null,
    val signal: String? = null,
    val confidence: Double? = null,
    val predictedReturn: Double? = null,
    val state: String? = null,
    val reason: String? = null,
    val modelVersion: String? = null,
    val modelReportId: String? = null,
)

/** 네 required component를 exact field set으로 고정한다. */
data class RuntimeSignalComponents(
    val ruleBaseline: RuntimeSignalComponent,
    val lstm: RuntimeSignalComponent,
    val lightgbm: RuntimeSignalComponent,
    val hmmRegime: RuntimeSignalComponent,
)

/** composite union의 application projection이다. */
data class RuntimeSignalComposite(
    val status: String,
    val signal: String? = null,
    val confidence: Double? = null,
    val predictedReturn: Double? = null,
    val reason: String? = null,
)

/** API serializer 직전의 runtime v1 root projection이다. */
data class RuntimeSignalResponse(
    val symbol: String,
    val asOf: Instant?,
    val timeframe: String,
    val modelReportId: String?,
    val composite: RuntimeSignalComposite,
    val components: RuntimeSignalComponents,
    val warnings: List<String>,
)

@Service
class SignalV2RuntimeService(
    private val readPort: SignalV2ProductionReadPort,
) {
    /**
     * symbol 하나만 받아 production pointer rows를 조합한다. evidence가 없으면 404/HOLD가 아니라
     * 네 component all-ABSTAIN을 반환하고 DB 판정 실패만 SIGNAL_UNAVAILABLE로 닫는다.
     */
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
        if (snapshot.rows.isEmpty()) {
            return allAbstain(symbol)
        }
        val latest = snapshot.latestCompletedSession ?: throw ApiException(ErrorCode.SIGNAL_UNAVAILABLE)
        val byProducer = snapshot.rows.associateBy { it.producer }
        if (byProducer.size != snapshot.rows.size || byProducer.keys.any { it !in REQUIRED_PRODUCERS }) {
            throw ApiException(ErrorCode.SIGNAL_UNAVAILABLE)
        }
        val components =
            RuntimeSignalComponents(
                ruleBaseline = component(byProducer["RULE_BASELINE"], "RULE_BASELINE", "return-engine", latest),
                lstm = component(byProducer["LSTM"], "LSTM", "return-engine", latest),
                lightgbm = component(byProducer["LIGHTGBM"], "LIGHTGBM", "decision-platform", latest),
                // HMM AVAILABLE은 S6 계약 전에는 DB signal row로 해석하지 않는다.
                hmmRegime = abstain("HMM", "decision-platform", byProducer["HMM"]?.reason ?: "MISSING_EVIDENCE"),
            )
        val available =
            listOf(components.ruleBaseline, components.lstm, components.lightgbm, components.hmmRegime)
                .filter { it.status == "AVAILABLE" }
        val allAvailable = available.size == 4
        val composite =
            if (allAvailable) {
                // 실제 composite 정책은 후속 승인 대상이므로 이 branch는 contract completeness용 fail-closed guard다.
                throw ApiException(ErrorCode.SIGNAL_UNAVAILABLE)
            } else {
                RuntimeSignalComposite(status = "ABSTAIN", reason = "REQUIRED_COMPONENT_UNAVAILABLE")
            }
        val newest = available.maxByOrNull { requireNotNull(it.asOf) }
        return RuntimeSignalResponse(
            symbol = symbol,
            asOf = newest?.asOf,
            timeframe = "1d",
            modelReportId = newest?.modelReportId,
            composite = composite,
            components = components,
            warnings = listOf("One or more required Signal components are unavailable."),
        )
    }

    private fun component(
        row: StoredSignalComponent?,
        producer: String,
        workspace: String,
        latest: LocalDate,
    ): RuntimeSignalComponent {
        if (row == null) {
            return abstain(producer, workspace, "MISSING_EVIDENCE")
        }
        if (row.sourceWorkspace != workspace) {
            throw ApiException(ErrorCode.SIGNAL_UNAVAILABLE)
        }
        if (row.sessionDate != latest) {
            return abstain(producer, workspace, "STALE_EVIDENCE")
        }
        if (row.status == "ABSTAIN") {
            return abstain(producer, workspace, row.reason ?: "PRODUCER_FAILED")
        }
        if (
            row.status != "AVAILABLE" ||
            row.asOf == null ||
            row.signal !in SIGNALS ||
            row.confidence == null ||
            !row.confidence.isFinite() ||
            row.confidence !in 0.0..1.0 ||
            (row.predictedReturn != null && !row.predictedReturn.isFinite()) ||
            row.modelVersion?.let { it.isEmpty() || it.length > 128 } == true ||
            row.modelReportId?.let { it.isEmpty() || it.length > 128 } == true
        ) {
            return abstain(producer, workspace, "UNIDENTIFIABLE_OUTPUT")
        }
        return RuntimeSignalComponent(
            status = "AVAILABLE",
            producer = producer,
            sourceWorkspace = workspace,
            asOf = row.asOf,
            signal = row.signal,
            confidence = row.confidence,
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
            warnings = listOf("No verified Signal component evidence is available."),
        )

    private fun abstain(
        producer: String,
        workspace: String,
        reason: String,
    ): RuntimeSignalComponent =
        RuntimeSignalComponent(
            status = "ABSTAIN",
            producer = producer,
            sourceWorkspace = workspace,
            reason = reason.takeIf { it in ABSTAIN_REASONS } ?: "PRODUCER_FAILED",
        )

    private companion object {
        val SYMBOL = Regex("^[0-9A-Z._:-]{1,20}$")
        val REQUIRED_PRODUCERS = setOf("RULE_BASELINE", "LSTM", "LIGHTGBM", "HMM")
        val SIGNALS = setOf("BUY", "HOLD", "SELL")
        val ABSTAIN_REASONS =
            setOf(
                "ARTIFACT_DRIFT",
                "CALIBRATION_FAILED",
                "MISSING_EVIDENCE",
                "POSTERIOR_BELOW_THRESHOLD",
                "PRODUCER_FAILED",
                "STALE_EVIDENCE",
                "UNIDENTIFIABLE_OUTPUT",
            )
    }
}
