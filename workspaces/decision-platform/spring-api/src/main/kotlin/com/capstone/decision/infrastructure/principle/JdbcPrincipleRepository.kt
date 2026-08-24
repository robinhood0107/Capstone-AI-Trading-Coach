package com.capstone.decision.infrastructure.principle

import com.capstone.decision.application.principle.HistoryCursor
import com.capstone.decision.application.principle.HistorySort
import com.capstone.decision.application.principle.OwnerCursor
import com.capstone.decision.application.principle.OwnerSort
import com.capstone.decision.application.principle.PrincipleActor
import com.capstone.decision.application.principle.PrincipleRepository
import com.capstone.decision.domain.principle.PrincipleCurrent
import com.capstone.decision.domain.principle.PrincipleId
import com.capstone.decision.domain.principle.PrincipleMode
import com.capstone.decision.domain.principle.PrinciplePreset
import com.capstone.decision.domain.principle.PrinciplePresetId
import com.capstone.decision.domain.principle.PrincipleStatus
import com.capstone.decision.domain.principle.PrincipleSummary
import com.capstone.decision.domain.principle.PrincipleVersion
import com.capstone.decision.domain.principle.PrincipleVersionId
import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityIssuer
import com.capstone.decision.infrastructure.security.ActorCapabilityRolePolicy
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.RowMapper
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import tools.jackson.databind.ObjectMapper
import java.sql.ResultSet
import java.time.OffsetDateTime
import java.time.ZoneOffset

// 모든 Principle SQL은 actor user_id를 같은 statement에 포함하며 sort fragment는 enum allowlist에서만 선택한다.
@Repository
class JdbcPrincipleRepository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val objectMapper: ObjectMapper,
    private val ruleJsonCodec: PrincipleRuleJsonCodec,
    private val actorCapabilityIssuer: ActorCapabilityIssuer,
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
        requireAuthorizedChange(
            """
            SELECT insert_principle_authorized(
              :capability, :userId, :principleId, :presetId, :title, :mode, :status,
              :version, :createdAt, :updatedAt
            )
            """.trimIndent(),
            mapOf(
                "principleId" to current.principleId.value,
                "capability" to
                    capability(
                        current.userId,
                        ActorCapabilityBinding.request(
                            "INSERT_PRINCIPLE",
                            "PRINCIPLE",
                            current.principleId.value,
                            ActorCapabilityRolePolicy.OWNER,
                            current.userId,
                            current.principleId.value,
                            current.presetId.value,
                            current.title,
                            current.mode.name,
                            current.status.name,
                            current.version.toString(),
                        ),
                    ),
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
        requireAuthorizedChange(
            """
            SELECT insert_principle_version_authorized_v2(
              :capability, :createdBy, :versionId, :principleId, :version, :presetId,
              :title, :mode, :status, :rulesJson, :changedFieldsJson, :createdAt
            )
            """.trimIndent(),
            mapOf(
                "versionId" to versionId.value,
                "capability" to
                    capability(
                        createdBy,
                        ActorCapabilityBinding.request(
                            "INSERT_PRINCIPLE_VERSION",
                            "PRINCIPLE_VERSION",
                            versionId.value,
                            ActorCapabilityRolePolicy.OWNER,
                            createdBy,
                            versionId.value,
                            version.principleId.value,
                            version.version.toString(),
                            version.presetId.value,
                            version.title,
                            version.mode.name,
                            version.status.name,
                            ruleJsonCodec.encode(version.rules),
                            objectMapper.writeValueAsString(version.changedFields),
                        ),
                    ),
                "principleId" to version.principleId.value,
                "version" to version.version,
                "presetId" to version.presetId.value,
                "title" to version.title,
                "mode" to version.mode.name,
                "status" to version.status.name,
                "rulesJson" to ruleJsonCodec.encode(version.rules),
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
        requireAuthorizedChange(
            """
            SELECT insert_principle_audit_authorized_v2(
              :capability, :userId, :requestId, :action, :principleId, :newVersion,
              :changedFieldsJson, :createdAt
            )
            """.trimIndent(),
            mapOf(
                "capability" to
                    capability(
                        actor.userId,
                        ActorCapabilityBinding.request(
                            "INSERT_PRINCIPLE_AUDIT",
                            "PRINCIPLE",
                            principleId.value,
                            ActorCapabilityRolePolicy.OWNER,
                            actor.userId,
                            actor.requestId,
                            action,
                            principleId.value,
                            newVersion.toString(),
                            objectMapper.writeValueAsString(changedFields),
                        ),
                    ),
                "userId" to actor.userId,
                "action" to action,
                "principleId" to principleId.value,
                "requestId" to actor.requestId,
                "newVersion" to newVersion,
                "changedFieldsJson" to objectMapper.writeValueAsString(changedFields),
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
                SELECT * FROM read_owned_principle_authorized(:capability, :userId, :principleId)
                """.trimIndent(),
                mapOf(
                    "principleId" to principleId.value,
                    "capability" to
                        capability(
                            userId,
                            ActorCapabilityBinding.target(
                                "READ_PRINCIPLE",
                                "PRINCIPLE",
                                principleId.value,
                                ActorCapabilityRolePolicy.OWNER,
                            ),
                        ),
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
        val parameters =
            MapSqlParameterSource()
                .addValue(
                    "capability",
                    capability(
                        userId,
                        ActorCapabilityBinding.request(
                            "LIST_PRINCIPLES",
                            "PRINCIPLE_LIST",
                            "principles",
                            ActorCapabilityRolePolicy.OWNER,
                            userId,
                            size.toString(),
                            sort.name,
                            after
                                ?.updatedAt
                                ?.toInstant()
                                ?.toEpochMilli()
                                ?.toString(),
                            after?.principleId,
                        ),
                    ),
                ).addValue("userId", userId)
                .addValue("limit", size)
                .addValue("sort", sort.name)
                .addValue("afterUpdatedAt", after?.updatedAt)
                .addValue("afterPrincipleId", after?.principleId)
        return jdbc().query(
            """
            SELECT * FROM list_owned_principles_authorized(
              :capability, :userId, :limit, :sort, :afterUpdatedAt, :afterPrincipleId
            )
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
                SELECT update_owned_principle_authorized(
                  :capability, :userId, :principleId, :expectedVersion,
                  :title, :mode, :status, :updatedAt
                ) AS current_version
                """.trimIndent(),
                mapOf(
                    "title" to title,
                    "capability" to
                        capability(
                            userId,
                            ActorCapabilityBinding.request(
                                "UPDATE_PRINCIPLE",
                                "PRINCIPLE",
                                principleId.value,
                                ActorCapabilityRolePolicy.OWNER,
                                userId,
                                principleId.value,
                                expectedVersion.toString(),
                                title,
                                mode.name,
                                status.name,
                            ),
                        ),
                    "mode" to mode.name,
                    "status" to status.name,
                    "updatedAt" to updatedAt,
                    "principleId" to principleId.value,
                    "userId" to userId,
                    "expectedVersion" to expectedVersion,
                ),
            ) { resultSet, _ ->
                resultSet.getInt("current_version").let { value ->
                    if (resultSet.wasNull()) null else value
                }
            }.singleOrNull()

    override fun listOwnedVersions(
        userId: String,
        principleId: PrincipleId,
        size: Int,
        sort: HistorySort,
        after: HistoryCursor?,
    ): List<PrincipleVersion> {
        val parameters =
            MapSqlParameterSource()
                .addValue(
                    "capability",
                    capability(
                        userId,
                        ActorCapabilityBinding.request(
                            "LIST_PRINCIPLE_VERSIONS",
                            "PRINCIPLE",
                            principleId.value,
                            ActorCapabilityRolePolicy.OWNER,
                            userId,
                            principleId.value,
                            size.toString(),
                            sort.name,
                            after?.version?.toString(),
                        ),
                    ),
                ).addValue("userId", userId)
                .addValue("principleId", principleId.value)
                .addValue("limit", size)
                .addValue("sort", sort.name)
                .addValue("afterVersion", after?.version)
        return jdbc().query(
            """
            SELECT * FROM list_owned_principle_versions_authorized(
              :capability, :userId, :principleId, :limit, :sort, :afterVersion
            )
            """.trimIndent(),
            parameters,
            versionRowMapper,
        )
    }

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable()
            ?: error("Principle JDBC access is unavailable without a configured DataSource.")

    private fun capability(
        userId: String,
        binding: ActorCapabilityBinding,
    ): String = actorCapabilityIssuer.issue(userId, binding)

    private fun requireAuthorizedChange(
        sql: String,
        parameters: Map<String, Any>,
    ) {
        check(jdbc().queryForObject(sql, parameters, Boolean::class.java) == true) {
            "Authorized principle mutation did not change one row."
        }
    }

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
                defaultRules = ruleJsonCodec.decode(resultSet.getString("rules_json")),
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
                rules = ruleJsonCodec.decode(resultSet.getString("rules_json")),
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
                rules = ruleJsonCodec.decode(resultSet.getString("rules_json")),
                changedFields = changedFields,
                createdAt = resultSet.kst("created_at"),
            )
        }

    companion object {
        private val KST = ZoneOffset.ofHours(9)
    }
}
