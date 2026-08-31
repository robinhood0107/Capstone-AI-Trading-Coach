package com.capstone.decision.infrastructure.vertex

import org.springframework.boot.context.properties.ConfigurationProperties
import java.nio.file.Path

/** S4.9 provider adapter는 Vertex transport를 유지하되 pre-S5 one-shot activation packet과 분리한다. */
@ConfigurationProperties("app.s4-9.strong-llm")
data class S49StrongLlmProperties(
    var enabled: Boolean = false,
    var modelId: String = "gemini-3.5-flash",
    var requestTimeoutMillis: Long = 30_000,
    var maxOutputTokens: Int = 4_096,
    var localRoot: String = "",
    var ownerConsentPolicySha256: String = "",
    var ownerConsentProcessorSetSha256: String = "",
) {
    fun validateEnabled() {
        if (!enabled) return
        require(RagV2VertexProperties.MODEL_ID.matches(modelId))
        require(requestTimeoutMillis in 1_000..30_000)
        // 활성화 패킷의 출력 상한과 같은 폭을 쓴다. 통제는 상한이 아니라 호출 횟수로 한다.
        require(maxOutputTokens in 256..32_768)
        val root = Path.of(localRoot)
        require(root.isAbsolute && root.normalize() == root)
        require(ownerConsentPolicySha256.isEmpty() || ownerConsentPolicySha256.matches(SHA256))
        require(ownerConsentProcessorSetSha256.isEmpty() || ownerConsentProcessorSetSha256.matches(SHA256))
    }

    private companion object {
        val SHA256 = Regex("^[0-9a-f]{64}$")
    }
}
