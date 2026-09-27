package com.capstone.decision.application.strongllm

import com.capstone.decision.application.automation.AutomationAiProviderPolicy
import com.capstone.decision.application.automation.AutomationEvidenceProvider
import com.capstone.decision.application.security.ActorRlsScopePort
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.transaction.PlatformTransactionManager
import org.springframework.transaction.support.TransactionTemplate

/** 자기 AI 검토 사용량. 한국 시각 기준 오늘과 이번 달, 자기 키와 공용 키를 나눠 센다. */
data class AiReviewUsage(
    val ownToday: Long,
    val sharedToday: Long,
    val ownMonth: Long,
    val sharedMonth: Long,
)

/**
 * 설정 화면 "내 Vertex 키" 가 보여 줄 사실. 키 자체·프로젝트 전체는 없다 - 키 ID 끝 네 글자뿐이다.
 *
 * `effectiveSource` 는 무장·실행 경로와 같은 규칙으로 계산한다: OWN(자기 키) → SHARED(공용 허용 시) → NONE.
 * NONE 이고 AI 검토가 켜져 있으면 자동매매 시작이 AI_PROVIDER_NOT_READY 로 막힌다.
 */
data class AiReviewStatus(
    val aiJudgementEnabled: Boolean,
    val ownKeyRegistered: Boolean,
    val ownKeyLast4: String?,
    val sharedAllowed: Boolean,
    val effectiveSource: String,
    val usage: AiReviewUsage,
)

@Service
class AiReviewStatusService(
    private val settings: StrongLlmSettingsService,
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val actorRlsScope: ActorRlsScopePort,
    private val policy: AutomationAiProviderPolicy,
    private val evidenceProvider: ObjectProvider<AutomationEvidenceProvider>,
    private val transactionManagerProvider: ObjectProvider<PlatformTransactionManager>,
) {
    fun status(ownerUserId: String): AiReviewStatus {
        val current = settings.read(ownerUserId)
        val shared = policy.operatorProviderReady(transportReady = evidenceProvider.getIfAvailable() != null)
        val own = current.primaryKeyLast4 != null && current.provider == "vertex"
        return AiReviewStatus(
            aiJudgementEnabled = current.aiJudgementEnabled,
            ownKeyRegistered = own,
            ownKeyLast4 = current.primaryKeyLast4.takeIf { own },
            sharedAllowed = shared,
            effectiveSource =
                when {
                    own -> "OWN"
                    shared -> "SHARED"
                    else -> "NONE"
                },
            usage = usage(ownerUserId),
        )
    }

    /** 소유자 범위는 트랜잭션 안에서만 열린다. 자기 호출이라 프록시 대신 명시 트랜잭션을 쓴다. */
    fun usage(ownerUserId: String): AiReviewUsage {
        val jdbc = jdbcProvider.getIfAvailable() ?: throw StrongLlmSettingsUnavailableException()
        val manager = transactionManagerProvider.getIfAvailable() ?: throw StrongLlmSettingsUnavailableException()
        return TransactionTemplate(manager).execute { readUsage(jdbc, ownerUserId) } ?: AiReviewUsage(0, 0, 0, 0)
    }

    private fun readUsage(
        jdbc: NamedParameterJdbcTemplate,
        ownerUserId: String,
    ): AiReviewUsage {
        actorRlsScope.open(jdbc, ownerUserId, "READ_AI_REVIEW_USAGE", "OWNER", ownerUserId)
        return jdbc
            .query(
                "SELECT own_today, shared_today, own_month, shared_month FROM read_owner_ai_usage_v1(:owner)",
                mapOf("owner" to ownerUserId),
            ) { row, _ ->
                AiReviewUsage(
                    ownToday = row.getLong("own_today"),
                    sharedToday = row.getLong("shared_today"),
                    ownMonth = row.getLong("own_month"),
                    sharedMonth = row.getLong("shared_month"),
                )
            }.singleOrNull() ?: AiReviewUsage(0, 0, 0, 0)
    }
}
