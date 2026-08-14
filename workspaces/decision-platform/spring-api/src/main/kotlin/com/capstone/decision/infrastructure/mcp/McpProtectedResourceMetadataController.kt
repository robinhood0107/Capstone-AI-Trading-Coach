package com.capstone.decision.infrastructure.mcp

import io.swagger.v3.oas.annotations.Hidden
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RestController

/** RFC 9728 metadata advertises one local authorization server and the fixed `/mcp` resource. */
@RestController
@Hidden
@ConditionalOnProperty(name = ["app.s4-9.mcp-oauth.enabled"], havingValue = "true")
class McpProtectedResourceMetadataController(
    private val properties: McpOAuthProperties,
) {
    @GetMapping("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp")
    fun metadata(): ResponseEntity<Map<String, Any>> =
        ResponseEntity.ok(
            linkedMapOf(
                "resource" to properties.resourceUri,
                "authorization_servers" to listOf(properties.issuer),
                "scopes_supported" to SCOPES,
                "bearer_methods_supported" to listOf("header"),
            ),
        )

    private companion object {
        val SCOPES =
            listOf("mcp:rag.public", "mcp:rag.owner", "mcp:web.read", "mcp:answer.validate", "mcp:history.write")
    }
}
