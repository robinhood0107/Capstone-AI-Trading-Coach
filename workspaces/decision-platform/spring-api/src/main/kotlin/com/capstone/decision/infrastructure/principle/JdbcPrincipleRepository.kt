package com.capstone.decision.infrastructure.principle

import com.capstone.decision.application.principle.HistorySort
import com.capstone.decision.application.principle.OwnerSort
import com.capstone.decision.application.principle.PrincipleActor
import com.capstone.decision.application.principle.PrincipleRepository
import com.capstone.decision.domain.principle.PrincipleCurrent
import com.capstone.decision.domain.principle.PrincipleId
import com.capstone.decision.domain.principle.PrincipleMode
import com.capstone.decision.domain.principle.PrinciplePreset
import com.capstone.decision.domain.principle.PrinciplePresetId
import com.capstone.decision.domain.principle.PrincipleRule
import com.capstone.decision.domain.principle.PrincipleStatus
import com.capstone.decision.domain.principle.PrincipleSummary
import com.capstone.decision.domain.principle.PrincipleVersion
import com.capstone.decision.domain.principle.PrincipleVersionId
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.RowMapper
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.sql.ResultSet
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.UUID

// 모든 Principle SQL은 actor user_id를 같은 statement에 포함하며 sort fragment는 enum allowlist에서만 선택한다.
@Repository
class JdbcPrincipleRepository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val objectMapper: ObjectMapper,
) : PrincipleRepository {
    override fun listActivePresets(): List<PrinciplePreset> =
        jdbc().query(
            """
            SELECT display_order, preset_id, name_ko, name_en, description_ko, description_en,
                   mode, rules_json::text AS rules_json
            FROM principle_presets
            WHERE is_active = true
            ORDER BY display_order
            """.trimIndent(),
            presetRowMapper,
        )

    override fun findActivePreset(presetId: PrinciplePresetId): PrinciplePreset? =
        jdbc()
            .query(
                """
                SELECT display_order, preset_id, name_ko, name_en, description_ko, description_en,
                       mode, rules_json::text AS rules_json
                FROM principle_presets
                WHERE preset_id = :presetId
                  AND is_active = true
                """.trimIndent(),
                mapOf("presetId" to presetId.value),
                presetRowMapper,
            ).singleOrNull()

    override fun insertPrinciple(current: PrincipleCurrent) {
        jdbc().update(
            """
            INSERT INTO principles (
              principle_id, user_id, preset_id, title, mode, status, current_version, created_at, updated_at
            )
            VALUES (
              :principleId, :userId, :presetId, :title, :mode, :status, :version, :createdAt, :updatedAt
            )
            """.trimIndent(),
            mapOf(
                "principleId" to current.principleId.value,
                "userId" to current.userId,
                "presetId" to current.presetId.value,
                "title" to current.title,
                "mode" to current.mode.name,
                "status" to current.status.name,
                "version" to current.version,
                "createdAt" to current.createdAt,
                "updatedAt" to current.updatedAt,
            ),
        )
    }

    override fun insertVersion(
        versionId: PrincipleVersionId,
        version: PrincipleVersion,
        createdBy: String,
    ) {
        jdbc().update(
            """
            INSERT INTO principle_versions (
              principle_version_id, principle_id, version, preset_id, title, mode, status,
              rules_json, changed_fields, created_by, created_at
            )
            VALUES (
              :versionId, :principleId, :version, :presetId, :title, :mode, :status,
              CAST(:rulesJson AS jsonb),
              ARRAY(SELECT jsonb_array_elements_text(CAST(:changedFieldsJson AS jsonb))),
              :createdBy, :createdAt
            )
            """.trimIndent(),
            mapOf(
                "versionId" to versionId.value,
                "principleId" to version.principleId.value,
                "version" to version.version,
                "presetId" to version.presetId.value,
                "title" to version.title,
                "mode" to version.mode.name,
                "status" to version.status.name,
                "rulesJson" to rulesJson(version.rules),
                "changedFieldsJson" to objectMapper.writeValueAsString(version.changedFields),
                "createdBy" to createdBy,
                "createdAt" to version.createdAt,
            ),
        )
    }

    override fun insertAudit(
        actor: PrincipleActor,
        action: String,
        principleId: PrincipleId,
        newVersion: Int,
        changedFields: List<String>,
        createdAt: OffsetDateTime,
    ) {
        val payload =
            mapOf(
                "principleId" to principleId.value,
                "newVersion" to newVersion,
                "changedFields" to changedFields,
            )
        jdbc().update(
            """
            INSERT INTO audit_logs (
              audit_log_id, user_id, actor_role, action, target_type, target_id,
              request_id, payload_json, created_at
            )
            VALUES (
              :auditId, :userId, :actorRole, :action, 'PRINCIPLE', :principleId,
              :requestId, CAST(:payloadJson AS jsonb), :createdAt
            )
            """.trimIndent(),
            mapOf(
                "auditId" to "aud_${UUID.randomUUID().toString().replace("-", "")}",
                "userId" to actor.userId,
                "actorRole" to actor.role,
                "action" to action,
                "principleId" to principleId.value,
                "requestId" to actor.requestId,
                "payloadJson" to objectMapper.writeValueAsString(payload),
                "createdAt" to createdAt,
            ),
        )
    }

    override fun findOwnedCurrent(
        userId: String,
        principleId: PrincipleId,
    ): PrincipleCurrent? =
        jdbc()
            .query(
                """
                SELECT p.principle_id, p.user_id, p.preset_id, p.title, p.mode, p.status,
                       p.current_version, p.created_at, p.updated_at,
                       v.rules_json::text AS rules_json
                FROM principles p
                JOIN principle_versions v
                  ON v.principle_id = p.principle_id
                 AND v.version = p.current_version
                WHERE p.principle_id = :principleId
                  AND p.user_id = :userId
                """.trimIndent(),
                mapOf(
                    "principleId" to principleId.value,
                    "userId" to userId,
                ),
                currentRowMapper,
            ).singleOrNull()

    override fun listOwned(
        userId: String,
        size: Int,
        sort: OwnerSort,
        after: OwnerCursor?,
    ): List<PrincipleSummary> {
        val ascending = sort == OwnerSort.UPDATED_AT_ASC
        val comparison = if (ascending) ">" else "<"
        val direction = if (ascending) "ASC" else "DESC"
        val cursorClause =
            if (after == null) {
                ""
            } else {
                "AND (p.updated_at, p.principle_id) $comparison (:afterUpdatedAt, :afterPrincipleId)"
            }
        val parameters =
            MapSqlParameterSource()
                .addValue("userId", userId)
                .addValue("limit", size)
        if (after != null) {
            parameters
                .addValue("afterUpdatedAt", after.updatedAt)
                .addValue("afterPrincipleId", after.principleId)
        }
        return jdbc().query(
            """
            SELECT p.principle_id, p.preset_id, p.title, p.mode, p.status,
                   p.current_version, p.created_at, p.updated_at
            FROM principles p
            WHERE p.user_id = :userId
              $cursorClause
            ORDER BY p.updated_at $direction, p.principle_id $direction
            LIMIT :limit
            """.trimIndent(),
            parameters,
            summaryRowMapper,
        )
    }

    override fun updateOwnedCas(
        userId: String,
        principleId: PrincipleId,
        expectedVersion: Int,
        title: String,
        mode: PrincipleMode,
        status: PrincipleStatus,
        updatedAt: OffsetDateTime,
    ): Int? =
        jdbc()
            .query(
                """
                UPDATE principles
                SET title = :title,
                    mode = :mode,
                    status = :status,
                    current_version = current_version + 1,
                    updated_at = :updatedAt
                WHERE principle_id = :principleId
                  AND user_id = :userId
                  AND current_version = :expectedVersion
                  AND current_version < :maxVersion
                RETURNING current_version
                """.trimIndent(),
                mapOf(
                    "title" to title,
                    "mode" to mode.name,
                    "status" to status.name,
                    "updatedAt" to updatedAt,
                    "principleId" to principleId.value,
                    "userId" to userId,
                    "expectedVersion" to expectedVersion,
                    "maxVersion" to Int.MAX_VALUE,
                ),
            ) { resultSet, _ -> resultSet.getInt("current_version") }
            .singleOrNull()

    override fun listOwnedVersions(
        userId: String,
        principleId: PrincipleId,
        size: Int,
        sort: HistorySort,
        after: HistoryCursor?,
    ): List<PrincipleVersion> {
        val ascending = sort == HistorySort.VERSION_ASC
        val comparison = if (ascending) ">" else "<"
        val direction = if (ascending) "ASC" else "DESC"
        val cursorClause =
            if (after == null) {
                ""
            } else {
                "AND v.version $comparison :afterVersion"
            }
        val parameters =
            MapSqlParameterSource()
                .addValue("userId", userId)
                .addValue("principleId", principleId.value)
                .addValue("limit", size)
        if (after != null) {
            parameters.addValue("afterVersion", after.version)
        }
        return jdbc().query(
            """
            SELECT v.principle_id, v.version, v.preset_id, v.title, v.mode, v.status,
                   v.rules_json::text AS rules_json, v.changed_fields, v.created_at
            FROM principle_versions v
            JOIN principles p
              ON p.principle_id = v.principle_id
             AND p.user_id = :userId
            WHERE v.principle_id = :principleId
              $cursorClause
            ORDER BY v.version $direction
            LIMIT :limit
            """.trimIndent(),
            parameters,
            versionRowMapper,
        )
    }

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable()
            ?: error("Principle JDBC access is unavailable without a configured DataSource.")

    private fun rulesJson(rules: List<PrincipleRule>): String =
        objectMapper.writeValueAsString(
            rules.map { rule ->
                linkedMapOf(
                    "ruleId" to rule.ruleId,
                    "ruleType" to rule.ruleType,
                    "metric" to rule.metric,
                    "operator" to rule.operator,
                    "threshold" to rule.threshold,
                    "severity" to rule.severity,
                    "enabled" to rule.enabled,
                )
            },
        )

    private fun parseRules(raw: String): List<PrincipleRule> =
        objectMapper
            .readTree(raw)
            .values()
            .map { node -> node.toRule() }

    private fun JsonNode.toRule(): PrincipleRule =
        PrincipleRule(
            ruleId = path("ruleId").stringValue(),
            ruleType = path("ruleType").stringValue(),
            metric = path("metric").stringValue(),
            operator = path("operator").stringValue(),
            threshold = path("threshold").decimalValue(),
            severity = path("severity").stringValue(),
            enabled = path("enabled").booleanValue(),
        )

    private fun ResultSet.kst(column: String): OffsetDateTime = getObject(column, OffsetDateTime::class.java).withOffsetSameInstant(KST)

    private val presetRowMapper =
        RowMapper { resultSet, _ ->
            PrinciplePreset(
                order = resultSet.getInt("display_order"),
                presetId = PrinciplePresetId(resultSet.getString("preset_id")),
                nameKo = resultSet.getString("name_ko"),
                nameEn = resultSet.getString("name_en"),
                descriptionKo = resultSet.getString("description_ko"),
                descriptionEn = resultSet.getString("description_en"),
                mode = PrincipleMode.valueOf(resultSet.getString("mode")),
                defaultRules = parseRules(resultSet.getString("rules_json")),
            )
        }

    private val currentRowMapper =
        RowMapper { resultSet, _ ->
            PrincipleCurrent(
                principleId = PrincipleId(resultSet.getString("principle_id")),
                userId = resultSet.getString("user_id"),
                presetId = PrinciplePresetId(resultSet.getString("preset_id")),
                title = resultSet.getString("title"),
                mode = PrincipleMode.valueOf(resultSet.getString("mode")),
                status = PrincipleStatus.valueOf(resultSet.getString("status")),
                version = resultSet.getInt("current_version"),
                rules = parseRules(resultSet.getString("rules_json")),
                createdAt = resultSet.kst("created_at"),
                updatedAt = resultSet.kst("updated_at"),
            )
        }

    private val summaryRowMapper =
        RowMapper { resultSet, _ ->
            PrincipleSummary(
                principleId = PrincipleId(resultSet.getString("principle_id")),
                presetId = PrinciplePresetId(resultSet.getString("preset_id")),
                title = resultSet.getString("title"),
                mode = PrincipleMode.valueOf(resultSet.getString("mode")),
                status = PrincipleStatus.valueOf(resultSet.getString("status")),
                version = resultSet.getInt("current_version"),
                createdAt = resultSet.kst("created_at"),
                updatedAt = resultSet.kst("updated_at"),
            )
        }

    private val versionRowMapper =
        RowMapper { resultSet, _ ->
            val changedFields =
                (resultSet.getArray("changed_fields").array as Array<*>)
                    .map(Any?::toString)
            PrincipleVersion(
                principleId = PrincipleId(resultSet.getString("principle_id")),
                version = resultSet.getInt("version"),
                presetId = PrinciplePresetId(resultSet.getString("preset_id")),
                title = resultSet.getString("title"),
                mode = PrincipleMode.valueOf(resultSet.getString("mode")),
                status = PrincipleStatus.valueOf(resultSet.getString("status")),
                rules = parseRules(resultSet.getString("rules_json")),
                changedFields = changedFields,
                createdAt = resultSet.kst("created_at"),
            )
        }

    companion object {
        private val KST = ZoneOffset.ofHours(9)
    }
}
