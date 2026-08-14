package com.capstone.decision.infrastructure.mcp

import org.assertj.core.api.Assertions.assertThatCode
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermissions

class McpOAuthPropertiesTest {
    @TempDir
    lateinit var tempDir: Path

    @Test
    fun `enabled OAuth accepts distinct 0600 regular single-link files`() {
        val jwk = secret("mcp-signing.jwk")
        val clients = secret("mcp-clients.json")

        assertThatCode { properties(jwk, clients).validateEnabled() }.doesNotThrowAnyException()
    }

    @Test
    fun `enabled OAuth rejects permissive secret mode`() {
        val jwk = secret("mcp-signing.jwk")
        val clients = secret("mcp-clients.json")
        Files.setPosixFilePermissions(jwk, PosixFilePermissions.fromString("rw-r--r--"))

        assertThatThrownBy { properties(jwk, clients).validateEnabled() }
            .isInstanceOf(IllegalArgumentException::class.java)
    }

    @Test
    fun `enabled OAuth rejects symlink and hardlink secrets`() {
        val jwk = secret("mcp-signing.jwk")
        val clients = secret("mcp-clients.json")
        val symlink = tempDir.resolve("mcp-signing-link.jwk")
        Files.createSymbolicLink(symlink, jwk.fileName)

        assertThatThrownBy { properties(symlink, clients).validateEnabled() }
            .isInstanceOf(IllegalArgumentException::class.java)

        val hardlink = tempDir.resolve("mcp-signing-hardlink.jwk")
        Files.createLink(hardlink, jwk)
        assertThatThrownBy { properties(jwk, clients).validateEnabled() }
            .isInstanceOf(IllegalArgumentException::class.java)
    }

    @Test
    fun `enabled OAuth requires HTTPS except exact loopback development origins`() {
        val jwk = secret("mcp-signing.jwk")
        val clients = secret("mcp-clients.json")

        assertThatThrownBy {
            properties(jwk, clients).copy(issuer = "http://example.com", resourceUri = "http://example.com/mcp").validateEnabled()
        }.isInstanceOf(IllegalArgumentException::class.java)
        assertThatThrownBy {
            properties(jwk, clients).copy(resourceUri = "https://api.example.com/mcp?debug=true").validateEnabled()
        }.isInstanceOf(IllegalArgumentException::class.java)
    }

    private fun secret(name: String): Path =
        tempDir.resolve(name).also { path ->
            Files.writeString(path, "{}")
            Files.setPosixFilePermissions(path, PosixFilePermissions.fromString("rw-------"))
        }

    private fun properties(
        jwk: Path,
        clients: Path,
    ) = McpOAuthProperties(
        enabled = true,
        issuer = "http://127.0.0.1:8080",
        resourceUri = "http://127.0.0.1:8080/mcp",
        signingJwkPath = jwk.toString(),
        clientAllowlistPath = clients.toString(),
    )
}
