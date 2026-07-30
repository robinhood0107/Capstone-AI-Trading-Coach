package com.capstone.decision.application.rag

interface RagSourceRegistryPort {
    /**
     * 입력 actor는 인증된 JWT subject만 허용하며 DB definer 경계에서도 같은 subject binding을 재검증한다.
     * 출력은 source registry metadata만 포함하고 원문·hash·peer IP·fetch 세부정보는 반환하지 않는다.
     */
    fun listVisibleSources(actorUserId: String): RagSourceRegistryList
}
