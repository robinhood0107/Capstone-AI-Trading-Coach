package com.capstone.decision.application.rag

import org.springframework.stereotype.Service

@Service
class RagSourceRegistryService(
    private val sourceRegistryPort: RagSourceRegistryPort,
) {
    /**
     * S4.1 공개 경계는 인증된 사용자에게 registry metadata 목록만 제공한다.
     * 검색, 등록, 수정, 삭제, 모델 선택은 이 endpoint에서 다루지 않는다.
     */
    fun listSources(actorUserId: String): RagSourceRegistryList =
        try {
            sourceRegistryPort.listVisibleSources(actorUserId)
        } catch (exception: RagSourceRegistryUnavailableException) {
            throw exception
        } catch (exception: RuntimeException) {
            throw RagSourceRegistryUnavailableException(exception)
        }
}
