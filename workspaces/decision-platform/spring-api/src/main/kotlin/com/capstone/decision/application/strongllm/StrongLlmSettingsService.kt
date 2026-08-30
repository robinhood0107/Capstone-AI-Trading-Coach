package com.capstone.decision.application.strongllm

import com.capstone.decision.application.security.ActorRlsScopePort
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional

/** 소유자가 화면에서 고른 Strong LLM 설정. 키는 여기에 없다. */
data class StrongLlmOwnerSettings(
    val provider: String,
    val fallbackProvider: String?,
    val modelId: String?,
    val fallbackModelId: String?,
    val baseUrl: String?,
    val fallbackBaseUrl: String?,
    val answerLanguage: String,
    val dailyGenerateCallCap: Int,
    /** 키가 들어 있는지를 말하는 데 필요한 전부다. */
    val primaryKeyLast4: String?,
    val fallbackKeyLast4: String?,
)

/**
 * 설정 쓰기 명령. `apiKey`는 **쓰기 전용**이며 어떤 응답에도 담기지 않는다.
 *
 * `null`은 "바꾸지 않는다", 빈 문자열은 "지운다"를 뜻한다. 두 가지를 하나로 합치면 설정만
 * 바꾸려는 요청이 이미 저장된 키를 조용히 지운다.
 */
data class PutStrongLlmSettingsCommand(
    val provider: String,
    val fallbackProvider: String?,
    val modelId: String?,
    val fallbackModelId: String?,
    val baseUrl: String?,
    val fallbackBaseUrl: String?,
    val answerLanguage: String,
    val dailyGenerateCallCap: Int,
    val apiKey: String?,
    val fallbackApiKey: String?,
)

class StrongLlmSettingsUnavailableException : RuntimeException("STRONG_LLM_SETTINGS_UNAVAILABLE")

@Service
class StrongLlmSettingsService(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val actorRlsScope: ActorRlsScopePort,
    private val crypto: StrongLlmCredentialPort,
) {
    @Transactional
    fun read(ownerUserId: String): StrongLlmOwnerSettings {
        val jdbc = jdbc()
        openScope(jdbc, ownerUserId, "READ_STRONG_LLM_SETTINGS")
        val last4 =
            jdbc
                .query(
                    "SELECT slot, key_last4 FROM read_strong_llm_owner_key_last4_v1(:ownerUserId)",
                    mapOf("ownerUserId" to ownerUserId),
                ) { row, _ -> row.getString("slot") to row.getString("key_last4") }
                .toMap()
        return jdbc
            .query(
                """
                SELECT provider, fallback_provider, model_id, fallback_model_id,
                       base_url, fallback_base_url, answer_language, daily_generate_call_cap
                FROM strong_llm_owner_settings
                WHERE owner_user_id = :ownerUserId
                """.trimIndent(),
                mapOf("ownerUserId" to ownerUserId),
            ) { row, _ ->
                StrongLlmOwnerSettings(
                    provider = row.getString("provider"),
                    fallbackProvider = row.getString("fallback_provider"),
                    modelId = row.getString("model_id"),
                    fallbackModelId = row.getString("fallback_model_id"),
                    baseUrl = row.getString("base_url"),
                    fallbackBaseUrl = row.getString("fallback_base_url"),
                    answerLanguage = row.getString("answer_language"),
                    dailyGenerateCallCap = row.getInt("daily_generate_call_cap"),
                    primaryKeyLast4 = last4["PRIMARY"],
                    fallbackKeyLast4 = last4["FALLBACK"],
                )
            }.singleOrNull()
            // 아직 고른 적이 없으면 배포 기본값을 그대로 보여준다. 화면이 빈 칸을 보고
            // "설정이 깨졌다"고 읽지 않도록 무엇이 쓰이고 있는지를 말한다.
            ?: StrongLlmOwnerSettings(
                provider = DEFAULT_PROVIDER,
                fallbackProvider = null,
                modelId = null,
                fallbackModelId = null,
                baseUrl = null,
                fallbackBaseUrl = null,
                answerLanguage = DEFAULT_LANGUAGE,
                dailyGenerateCallCap = DEFAULT_DAILY_CAP,
                primaryKeyLast4 = last4["PRIMARY"],
                fallbackKeyLast4 = last4["FALLBACK"],
            )
    }

    @Transactional
    fun put(
        ownerUserId: String,
        command: PutStrongLlmSettingsCommand,
    ) {
        val jdbc = jdbc()
        openScope(jdbc, ownerUserId, "PUT_STRONG_LLM_SETTINGS")
        jdbc.update(
            """
            SELECT put_strong_llm_owner_settings_v1(
              :ownerUserId, :provider, :fallbackProvider, :modelId, :fallbackModelId,
              :baseUrl, :fallbackBaseUrl, :answerLanguage, :dailyGenerateCallCap
            )
            """.trimIndent(),
            mapOf(
                "ownerUserId" to ownerUserId,
                "provider" to command.provider,
                "fallbackProvider" to command.fallbackProvider,
                "modelId" to command.modelId,
                "fallbackModelId" to command.fallbackModelId,
                "baseUrl" to command.baseUrl,
                "fallbackBaseUrl" to command.fallbackBaseUrl,
                "answerLanguage" to command.answerLanguage,
                "dailyGenerateCallCap" to command.dailyGenerateCallCap,
            ),
        )
        applyKey(jdbc, ownerUserId, "PRIMARY", command.apiKey)
        applyKey(jdbc, ownerUserId, "FALLBACK", command.fallbackApiKey)
    }

    private fun applyKey(
        jdbc: NamedParameterJdbcTemplate,
        ownerUserId: String,
        slot: String,
        apiKey: String?,
    ) {
        if (apiKey == null) {
            return
        }
        if (apiKey.isEmpty()) {
            jdbc.update(
                "SELECT delete_strong_llm_owner_credential_v1(:ownerUserId, :slot)",
                mapOf("ownerUserId" to ownerUserId, "slot" to slot),
            )
            return
        }
        val sealed = crypto.seal(ownerUserId, slot, apiKey)
        jdbc.update(
            """
            SELECT put_strong_llm_owner_credential_v1(
              :ownerUserId, :slot, :kekVersion, :wrapNonce, :wrappedDek, :wrapTag,
              :keyNonce, :keyCiphertext, :keyTag, :keyLast4
            )
            """.trimIndent(),
            mapOf(
                "ownerUserId" to ownerUserId,
                "slot" to slot,
                "kekVersion" to sealed.kekVersion,
                "wrapNonce" to sealed.wrapNonce,
                "wrappedDek" to sealed.wrappedDek,
                "wrapTag" to sealed.wrapTag,
                "keyNonce" to sealed.keyNonce,
                "keyCiphertext" to sealed.keyCiphertext,
                "keyTag" to sealed.keyTag,
                "keyLast4" to sealed.keyLast4,
            ),
        )
    }

    private fun openScope(
        jdbc: NamedParameterJdbcTemplate,
        ownerUserId: String,
        operation: String,
    ) = actorRlsScope.open(jdbc, ownerUserId, operation, "OWNER", ownerUserId)

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable() ?: throw StrongLlmSettingsUnavailableException()

    private companion object {
        const val DEFAULT_PROVIDER = "vertex"
        const val DEFAULT_LANGUAGE = "ko"
        const val DEFAULT_DAILY_CAP = 50
    }
}
