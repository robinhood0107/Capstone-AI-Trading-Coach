package com.capstone.decision.infrastructure.vertex

import jakarta.validation.constraints.Max
import jakarta.validation.constraints.Min
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.validation.annotation.Validated
import java.nio.file.Path

/**
 * Vertex는 explicit local packet이 있을 때만 켜진다. credential을 보관하지 않으며, 환경에서 선택한
 * publisher model과 current repository/CI/security digest를 packet과 대조할 public binding만 제공한다.
 */
@ConfigurationProperties("app.rag-v2.vertex")
@Validated
data class RagV2VertexProperties(
    var enabled: Boolean = false,
    var modelId: String = "gemini-3.5-flash",
    var localRoot: String = "",
    var headCommit: String = "",
    var treeDigest: String = "",
    var ciDigest: String = "",
    var securityDigest: String = "",
    @field:Min(1_000)
    @field:Max(30_000)
    var requestTimeoutMillis: Long = 30_000,
) {
    fun validateEnabled() {
        if (!enabled) {
            return
        }
        val root = Path.of(localRoot)
        require(root.isAbsolute && root.normalize() == root) {
            "Vertex activation root must be an absolute normalized local path."
        }
        require(MODEL_ID.matches(modelId)) { "Vertex publisher model ID is invalid." }
        require(HEAD_COMMIT.matches(headCommit)) { "Vertex activation HEAD binding is invalid." }
        require(SHA256.matches(treeDigest)) { "Vertex activation tree binding is invalid." }
        require(SHA256.matches(ciDigest)) { "Vertex activation CI binding is invalid." }
        require(SHA256.matches(securityDigest)) { "Vertex activation security binding is invalid." }
        require(requestTimeoutMillis in 1_000..30_000)
    }

    companion object {
        val HEAD_COMMIT = Regex("^[0-9a-f]{40,64}$")
        val SHA256 = Regex("^[0-9a-f]{64}$")
        val MODEL_ID = Regex("^[a-z][a-z0-9.-]{2,127}$")
    }
}
