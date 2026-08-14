package com.capstone.decision.infrastructure.mcp

import org.springframework.boot.context.properties.ConfigurationProperties
import java.net.URI

/** S4.9 tool budget은 env로 낮출 수 있지만 absolute cap을 넘길 수 없다. */
@ConfigurationProperties("app.rag-v2.web")
data class RagWebToolProperties(
    var enabled: Boolean = false,
    var searxngBaseUrl: String = "http://127.0.0.1:58080",
    var receiptHmacKey: String = "",
    var conciseMaxSearches: Int = 1,
    var conciseMaxReads: Int = 3,
    var detailedMaxSearches: Int = 2,
    var detailedMaxReads: Int = 6,
    var absoluteMaxSearches: Int = 3,
    var absoluteMaxReads: Int = 8,
    var maxParallelReads: Int = 3,
    var maxToolRounds: Int = 3,
    var externalResearchWindowMinutes: Int = 15,
    var externalResearchMaxSearches: Int = 30,
    var externalResearchMaxReads: Int = 120,
    var externalResearchUserParallelReads: Int = 4,
    var externalResearchGlobalParallelReads: Int = 8,
    var externalResearchMaxContextsPerCaller: Int = 30,
    var externalResearchMaxTotalContexts: Int = 1_024,
) {
    fun validate() {
        val base = URI.create(searxngBaseUrl)
        require(
            base.scheme == "http" &&
                base.host in setOf("searxng", "127.0.0.1", "localhost") &&
                base.rawUserInfo == null &&
                base.rawQuery == null &&
                base.rawFragment == null,
        )
        require(conciseMaxSearches in 0..absoluteMaxSearches)
        require(detailedMaxSearches in 0..absoluteMaxSearches)
        require(conciseMaxReads in 0..absoluteMaxReads)
        require(detailedMaxReads in 0..absoluteMaxReads)
        require(absoluteMaxSearches in 0..3 && absoluteMaxReads in 0..8)
        require(maxParallelReads in 1..3 && maxToolRounds in 0..3)
        require(externalResearchWindowMinutes in 1..60)
        require(externalResearchMaxSearches in 0..30 && externalResearchMaxReads in 0..120)
        require(externalResearchUserParallelReads in 1..4)
        require(externalResearchGlobalParallelReads in externalResearchUserParallelReads..8)
        require(externalResearchMaxContextsPerCaller in 1..30)
        require(externalResearchMaxTotalContexts in externalResearchMaxContextsPerCaller..1_024)
        if (enabled) require(receiptHmacKey.toByteArray(Charsets.UTF_8).size >= 32)
    }
}

data class RagToolBudget(
    val maxSearches: Int,
    val maxReads: Int,
    val maxToolRounds: Int,
    val maxParallelReads: Int,
)

fun RagWebToolProperties.budget(answerMode: String): RagToolBudget {
    validate()
    return when (answerMode) {
        "CONCISE" -> RagToolBudget(conciseMaxSearches, conciseMaxReads, maxToolRounds, maxParallelReads)
        "DETAILED" -> RagToolBudget(detailedMaxSearches, detailedMaxReads, maxToolRounds, maxParallelReads)
        else -> throw IllegalArgumentException("Unsupported answer mode")
    }
}
