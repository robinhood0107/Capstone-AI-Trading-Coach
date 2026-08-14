package com.capstone.decision.infrastructure.mcp

import org.springframework.boot.context.properties.ConfigurationProperties
import java.net.URI
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermissions

/** MCP OAuth key/client files are local-only 0600 regular files and share no material with the API JWT. */
@ConfigurationProperties("app.s4-9.mcp-oauth")
data class McpOAuthProperties(
    var enabled: Boolean = false,
    var issuer: String = "http://127.0.0.1:8080",
    var resourceUri: String = "http://127.0.0.1:8080/mcp",
    var signingJwkPath: String = "",
    var clientAllowlistPath: String = "",
) {
    fun validateEnabled() {
        if (!enabled) return
        val issuerUri = URI.create(issuer)
        val resource = URI.create(resourceUri)
        require(issuerUri.scheme == "https" || isLoopback(issuerUri))
        require(resource.scheme == "https" || isLoopback(resource))
        require(resource.path == "/mcp" && resource.rawQuery == null && resource.rawFragment == null)
        verifySecretFile(Path.of(signingJwkPath))
        verifySecretFile(Path.of(clientAllowlistPath))
        require(Path.of(signingJwkPath).normalize() != Path.of(clientAllowlistPath).normalize())
    }

    private fun verifySecretFile(path: Path) {
        require(path.isAbsolute && path.normalize() == path)
        require(Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS) && Files.getAttribute(path, "unix:nlink") == 1)
        require(PosixFilePermissions.toString(Files.getPosixFilePermissions(path, LinkOption.NOFOLLOW_LINKS)) == "rw-------")
    }

    private fun isLoopback(uri: URI): Boolean = uri.scheme == "http" && uri.host in setOf("127.0.0.1", "localhost")
}
