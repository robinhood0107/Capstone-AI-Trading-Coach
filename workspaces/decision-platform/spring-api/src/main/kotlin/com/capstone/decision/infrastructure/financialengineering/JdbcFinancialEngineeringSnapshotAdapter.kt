package com.capstone.decision.infrastructure.financialengineering

import com.capstone.decision.application.financialengineering.FinancialEngineeringSnapshotPort
import com.capstone.decision.application.financialengineering.StoredFinancialEngineeringSnapshot
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import tools.jackson.databind.ObjectMapper
import java.time.Instant

@Repository
class JdbcFinancialEngineeringSnapshotAdapter(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val objectMapper: ObjectMapper,
) : FinancialEngineeringSnapshotPort {
    override fun loadLatest(
        symbol: String,
        evaluationAsOf: Instant,
    ): StoredFinancialEngineeringSnapshot? {
        require(SYMBOL.matches(symbol))
        val rows =
            jdbc()
                .query(
                    """
                    SELECT snapshot_id, schema_version, symbol, session_date, as_of,
                           available_at, source_manifest_hash, config_hash,
                           numeric_payload_hash, artifact_hash, availability, quality,
                           staleness, numeric_payload::text, report_artifact_hash
                    FROM read_financial_engineering_snapshot(:symbol, :evaluationAsOf)
                    """.trimIndent(),
                    mapOf("symbol" to symbol, "evaluationAsOf" to evaluationAsOf),
                ) { result, _ ->
                    StoredFinancialEngineeringSnapshot(
                        snapshotId = result.getString("snapshot_id"),
                        schemaVersion = result.getInt("schema_version"),
                        symbol = result.getString("symbol"),
                        sessionDate = result.getObject("session_date", java.time.LocalDate::class.java),
                        asOf = result.getTimestamp("as_of").toInstant(),
                        availableAt = result.getTimestamp("available_at").toInstant(),
                        sourceManifestHash = result.getString("source_manifest_hash"),
                        configHash = result.getString("config_hash"),
                        numericPayloadHash = result.getString("numeric_payload_hash"),
                        artifactHash = result.getString("artifact_hash"),
                        availability = result.getString("availability"),
                        quality = result.getString("quality"),
                        staleness = result.getString("staleness"),
                        numericPayload = objectMapper.readTree(result.getString("numeric_payload")),
                        reportArtifactHash = result.getString("report_artifact_hash"),
                    )
                }
        require(rows.size <= 1)
        return rows.singleOrNull()?.also { snapshot ->
            require(snapshot.symbol == symbol)
            require(!snapshot.availableAt.isAfter(evaluationAsOf))
            require(snapshot.schemaVersion == 1)
            require(
                listOf(
                    snapshot.sourceManifestHash,
                    snapshot.configHash,
                    snapshot.numericPayloadHash,
                    snapshot.artifactHash,
                    snapshot.reportArtifactHash,
                ).all(SHA256::matches),
            )
        }
    }

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable()
            ?: throw IllegalStateException("financial engineering storage is unavailable")

    private companion object {
        val SYMBOL = Regex("^[0-9A-Z./-]{1,32}$")
        val SHA256 = Regex("^[0-9a-f]{64}$")
    }
}
