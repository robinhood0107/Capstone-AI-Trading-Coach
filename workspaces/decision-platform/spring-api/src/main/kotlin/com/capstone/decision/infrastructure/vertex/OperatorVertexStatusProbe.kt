package com.capstone.decision.infrastructure.vertex

import org.springframework.beans.factory.ObjectProvider
import org.springframework.stereotype.Component

/** 관리자 콘솔이 보는 공용 Vertex 상태. 서비스 계정·토큰 값은 없다. */
data class OperatorVertexStatus(
    val configured: Boolean,
    val projectId: String?,
    val modelId: String?,
    val reachable: Boolean,
)

/**
 * 공용 Vertex 가 이 배포에 구성됐는지, 실제로 Google OAuth 토큰을 받을 수 있는지를 한 번 확인한다.
 * 토큰은 기존 메모리 캐시를 그대로 쓰고 값은 즉시 지운다. 프로젝트 ID 는 토큰 응답이 아니라 서비스 계정에서
 * 읽힌 값이며 비밀이 아니다.
 */
@Component
class OperatorVertexStatusProbe internal constructor(
    private val properties: S49StrongLlmProperties,
    private val tokenProvider: ObjectProvider<S49VertexAccessTokenProvider>,
) {
    fun status(): OperatorVertexStatus {
        val provider = tokenProvider.getIfAvailable()
        if (!properties.enabled || provider == null) {
            return OperatorVertexStatus(configured = false, projectId = null, modelId = null, reachable = false)
        }
        return try {
            val token = provider.acquire()
            token.value.fill(0)
            OperatorVertexStatus(true, token.projectId, properties.modelId, reachable = true)
        } catch (_: Exception) {
            OperatorVertexStatus(true, projectId = null, modelId = properties.modelId, reachable = false)
        }
    }
}
