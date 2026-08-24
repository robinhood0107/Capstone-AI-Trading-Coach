package com.capstone.decision.infrastructure.mcp

data class McpRefreshClaim(
    val clientId: String,
    val ownerUserId: String,
    val securityVersion: Long,
    val resourceUri: String,
    val scopes: Set<String>,
)

class McpRefreshClaimContext {
    private val current = ThreadLocal<McpRefreshClaim>()

    fun bind(claim: McpRefreshClaim) {
        check(current.get() == null)
        current.set(claim)
    }

    fun requireCurrent(): McpRefreshClaim = requireNotNull(current.get()) { "Refresh claim is required" }

    fun optional(): McpRefreshClaim? = current.get()

    fun clear() {
        current.remove()
    }
}
