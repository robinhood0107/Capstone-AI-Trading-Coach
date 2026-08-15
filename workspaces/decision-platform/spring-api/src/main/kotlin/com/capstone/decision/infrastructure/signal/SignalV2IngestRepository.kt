package com.capstone.decision.infrastructure.signal

import com.capstone.decision.application.signal.SignalStorageUnavailableException
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import org.springframework.transaction.annotation.Transactional
import java.math.BigDecimal
import java.time.Instant
import java.time.LocalDate

/** Python safe validator를 통과한 한 internal artifact row의 exact DML 입력이다. */
data class SignalV2IngestCommand(
    val contractVersion: String,
    val producer: String,
    val sourceWorkspace: String,
    val symbol: String,
    val sessionDate: LocalDate,
    val asOf: Instant?,
    val timeframe: String,
    val status: String,
    val reason: String?,
    val signal: String?,
    val confidence: BigDecimal?,
    val predictedReturn: BigDecimal?,
    val evaluationId: String,
    val modelVersion: String,
    val modelReportId: String,
    val artifactSha256: String,
    val payloadSha256: String,
    val provenanceSha256: String,
    val fixture: Boolean,
    val provenanceClass: String,
    val payloadCanonicalText: String,
)

/** overwrite 없는 INSERTED/REPLAYED 결과다. */
data class SignalV2IngestResult(
    val outcome: String,
    val signalId: String,
)

@Repository
class SignalV2IngestRepository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
) {
    /**
     * 모든 artifact row를 단일 transaction에서 DB-computed identity/payload digest 함수로 넣는다.
     * 한 row라도 conflict면 Spring transaction이 전체 batch를 rollback한다.
     */
    @Transactional
    fun ingestAll(commands: List<SignalV2IngestCommand>): List<SignalV2IngestResult> {
        if (commands.isEmpty()) {
            throw IllegalArgumentException("Signal v2 ingest batch must not be empty.")
        }
        val jdbc = jdbcProvider.getIfAvailable() ?: throw SignalStorageUnavailableException()
        return commands.map { command -> ingestOne(jdbc, command) }
    }

    private fun ingestOne(
        jdbc: NamedParameterJdbcTemplate,
        command: SignalV2IngestCommand,
    ): SignalV2IngestResult {
        val parameters =
            mapOf(
                "contractVersion" to command.contractVersion,
                "producer" to command.producer,
                "sourceWorkspace" to command.sourceWorkspace,
                "symbol" to command.symbol,
                "sessionDate" to command.sessionDate,
                "asOf" to command.asOf?.let { java.sql.Timestamp.from(it) },
                "timeframe" to command.timeframe,
                "status" to command.status,
                "reason" to command.reason,
                "signal" to command.signal,
                "confidence" to command.confidence,
                "predictedReturn" to command.predictedReturn,
                "evaluationId" to command.evaluationId,
                "modelVersion" to command.modelVersion,
                "modelReportId" to command.modelReportId,
                "artifactSha256" to command.artifactSha256,
                "payloadSha256" to command.payloadSha256,
                "provenanceSha256" to command.provenanceSha256,
                "fixture" to command.fixture,
                "provenanceClass" to command.provenanceClass,
                "payloadCanonicalText" to command.payloadCanonicalText,
            )
        return jdbc
            .query(
                """
                SELECT outcome, signal_id
                FROM ingest_signal_v2_exact(
                  :contractVersion, :producer, :sourceWorkspace, :symbol, :sessionDate,
                  :asOf, :timeframe, :status, :reason, :signal, :confidence, :predictedReturn,
                  :evaluationId, :modelVersion, :modelReportId, :artifactSha256,
                  :payloadSha256, :provenanceSha256, :fixture, :provenanceClass,
                  :payloadCanonicalText
                )
                """.trimIndent(),
                parameters,
            ) { result, _ ->
                SignalV2IngestResult(result.getString("outcome"), result.getString("signal_id"))
            }.single()
    }
}
