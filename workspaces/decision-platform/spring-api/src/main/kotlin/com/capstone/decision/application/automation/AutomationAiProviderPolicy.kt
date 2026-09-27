package com.capstone.decision.application.automation

import org.springframework.beans.factory.ObjectProvider
import org.springframework.beans.factory.annotation.Value
import org.springframework.stereotype.Component

/** AI 검토 한 번을 누구의 자격증명으로 부르는지. 사용량 행에 그대로 남는다. */
enum class AutomationAiCredentialSource {
    /** 사용자가 설정 화면에 등록한 자기 Vertex 서비스 계정. */
    OWNER,

    /** 배포 비밀에만 있는 운영자 공용 Vertex 서비스 계정. */
    OPERATOR,
}

/** 관리자 콘솔의 "공용 Vertex 사용 허용" 스위치(DB 한 행). */
fun interface OperatorVertexSwitchReader {
    fun enabled(): Boolean
}

/**
 * AI 검토 제공자를 고르는 규칙 하나를 무장 판정·상태 화면·실행 경로가 함께 쓴다.
 *
 * 순서는 "사용자 자기 키 → 공용 Vertex(허용된 경우만)" 이다. 공용 경로는 두 겹으로 연다:
 * - 배포 설정 `app.automation.ai.operator-vertex-fallback-enabled` 는 상한이다. 개인 스택(LOCAL)은
 *   기본값 false 라 예전 규칙(소유자 키 필수)과 같다.
 * - 관리자 콘솔 스위치(DB)가 그 안에서 켜고 끈다. 사용자별 과금을 시작할 때 이 스위치를 끄면 자기 키가
 *   없는 사용자는 무장 단계에서 AI_PROVIDER_NOT_READY 로 멈춘다 - 실행 중에 조용히 운영자 비용을 쓰지 않는다.
 */
@Component
class AutomationAiProviderPolicy(
    @Value("\${app.automation.ai.operator-vertex-fallback-enabled:false}")
    private val operatorVertexFallbackEnabled: Boolean = false,
    private val switchReader: ObjectProvider<OperatorVertexSwitchReader>? = null,
) {
    /** 배포 상한과 관리자 스위치가 모두 허용하는가. 스위치를 읽지 못하면 닫는다. */
    fun operatorAllowed(): Boolean {
        if (!operatorVertexFallbackEnabled) return false
        val reader = switchReader?.getIfAvailable() ?: return true
        return runCatching { reader.enabled() }.getOrDefault(false)
    }

    /** 배포 상한(설정) 자체. 관리자 화면이 "배포에서 막혀 있음"을 따로 말할 때 쓴다. */
    fun deploymentCeiling(): Boolean = operatorVertexFallbackEnabled

    /**
     * evidence transport 가 떠 있을 때만 공용 경로가 실제로 쓸 수 있다. transport 가 없으면 허용돼 있어도
     * 준비되지 않은 것이다.
     */
    fun operatorProviderReady(transportReady: Boolean): Boolean = transportReady && operatorAllowed()

    /**
     * 실행 직전의 선택. 자기 키가 있으면 언제나 그것을 먼저 쓴다. 없으면 공용 경로가 허용됐을 때만
     * 운영자, 아니면 null(부르지 않는다).
     */
    fun select(ownerCredentialUsable: Boolean): AutomationAiCredentialSource? =
        when {
            ownerCredentialUsable -> AutomationAiCredentialSource.OWNER
            operatorAllowed() -> AutomationAiCredentialSource.OPERATOR
            else -> null
        }
}
