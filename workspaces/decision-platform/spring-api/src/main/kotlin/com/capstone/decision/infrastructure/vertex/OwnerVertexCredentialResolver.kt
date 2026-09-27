package com.capstone.decision.infrastructure.vertex

import com.capstone.decision.application.automation.AutomationAiCredential
import com.capstone.decision.application.automation.AutomationAiCredentialSource
import com.capstone.decision.application.automation.AutomationAiProviderPolicy
import com.capstone.decision.application.security.ActorRlsScopePort
import com.capstone.decision.application.strongllm.StrongLlmCredentialPort
import com.capstone.decision.application.strongllm.StrongLlmSealedCredential
import com.capstone.decision.application.strongllm.VertexServiceAccountShape
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Component
import org.springframework.transaction.PlatformTransactionManager
import org.springframework.transaction.TransactionDefinition
import org.springframework.transaction.support.TransactionTemplate
import java.nio.charset.StandardCharsets

/**
 * 에이전트(RAG 질문)의 Vertex 자격증명 선택. 자동매매 AI 검토와 같은 규칙이다:
 * 사용자 자기 서비스 계정 → (배포 상한과 관리자 스위치가 허용할 때만) 공용 Vertex → 부르지 않음(null).
 *
 * 키는 자기 트랜잭션에서 소유자 범위를 열고 읽는다. 호출자가 [AutomationAiCredential.close] 로 지운다.
 */
@Component
class OwnerVertexCredentialResolver(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val actorRlsScope: ActorRlsScopePort,
    private val credentialPort: StrongLlmCredentialPort,
    private val policy: AutomationAiProviderPolicy,
    private val transactionManagerProvider: ObjectProvider<PlatformTransactionManager>,
) {
    fun resolve(ownerUserId: String): AutomationAiCredential? {
        val owned = runCatching { openOwn(ownerUserId) }.getOrNull()
        return when (policy.select(ownerCredentialUsable = owned != null)) {
            AutomationAiCredentialSource.OWNER -> AutomationAiCredential.owner(requireNotNull(owned))
            AutomationAiCredentialSource.OPERATOR -> AutomationAiCredential.operator()
            null -> null
        }
    }

    /** 사용량 한 줄. 집계용이며 호출을 막지 않는다. */
    fun recordUsage(
        ownerUserId: String,
        runId: String,
        source: AutomationAiCredentialSource,
        providerCalls: Int,
    ) {
        runCatching {
            val jdbc = jdbcProvider.getIfAvailable() ?: return
            transaction(TransactionDefinition.PROPAGATION_REQUIRES_NEW).executeWithoutResult {
                jdbc.queryForObject(
                    "SELECT public.record_agent_ai_usage_v1(:owner, :runId, :source, :calls)",
                    mapOf("owner" to ownerUserId, "runId" to runId, "source" to source.name, "calls" to providerCalls),
                    Boolean::class.java,
                )
            }
        }
    }

    private fun openOwn(ownerUserId: String): ByteArray? {
        val jdbc = jdbcProvider.getIfAvailable() ?: return null
        val sealed =
            transaction(TransactionDefinition.PROPAGATION_REQUIRES_NEW).execute {
                actorRlsScope.open(jdbc, ownerUserId, "READ_STRONG_LLM_SETTINGS", "OWNER", ownerUserId)
                jdbc
                    .query(
                        "SELECT * FROM read_strong_llm_owner_credential_v1(:owner,'PRIMARY')",
                        mapOf("owner" to ownerUserId),
                    ) { row, _ ->
                        StrongLlmSealedCredential(
                            kekVersion = row.getString("kek_version"),
                            wrapNonce = row.getBytes("wrap_nonce"),
                            wrappedDek = row.getBytes("wrapped_dek"),
                            wrapTag = row.getBytes("wrap_tag"),
                            keyNonce = row.getBytes("key_nonce"),
                            keyCiphertext = row.getBytes("key_ciphertext"),
                            keyTag = row.getBytes("key_tag"),
                            keyLast4 = "",
                        )
                    }.singleOrNull()
            } ?: return null
        val opened = credentialPort.open(ownerUserId, "PRIMARY", sealed)
        if (!VertexServiceAccountShape.isValid(String(opened, StandardCharsets.US_ASCII))) {
            opened.fill(0)
            return null
        }
        return opened
    }

    private fun transaction(propagation: Int): TransactionTemplate =
        TransactionTemplate(
            transactionManagerProvider.getIfAvailable() ?: error("STRONG_LLM_TRANSACTIONS_UNAVAILABLE"),
        ).apply { propagationBehavior = propagation }
}
