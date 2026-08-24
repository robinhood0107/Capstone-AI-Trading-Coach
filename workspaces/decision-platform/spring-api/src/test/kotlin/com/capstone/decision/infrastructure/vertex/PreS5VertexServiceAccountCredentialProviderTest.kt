package com.capstone.decision.infrastructure.vertex

import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import tools.jackson.databind.json.JsonMapper
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermissions
import java.security.KeyPairGenerator
import java.util.Base64

class PreS5VertexServiceAccountCredentialProviderTest {
    @TempDir
    lateinit var root: Path

    @Test
    fun `fixed 0600 service account JSON supplies project email and signing key without ambient credentials`() {
        secureDirectory(root)
        val secrets = Files.createDirectory(root.resolve("secrets"), PosixFilePermissions.asFileAttribute(DIRECTORY_PERMISSIONS))
        val credential = secrets.resolve("pre-s5-vertex-service-account.json")
        Files.writeString(credential, credentialJson())
        Files.setPosixFilePermissions(credential, FILE_PERMISSIONS)

        val loaded = PreS5VertexServiceAccountCredentialProvider(RagV2VertexProperties(localRoot = root.toString())).acquire()

        assertThat(loaded.projectId).isEqualTo("project-test-123")
        assertThat(loaded.clientEmail).isEqualTo("vertex-test@project-test-123.iam.gserviceaccount.com")
        assertThat(loaded.privateKey.algorithm).isEqualTo("RSA")
    }

    @Test
    fun `group readable or linked credential fails closed`() {
        secureDirectory(root)
        val secrets = Files.createDirectory(root.resolve("secrets"), PosixFilePermissions.asFileAttribute(DIRECTORY_PERMISSIONS))
        val credential = secrets.resolve("pre-s5-vertex-service-account.json")
        Files.writeString(credential, credentialJson())
        Files.setPosixFilePermissions(credential, PosixFilePermissions.fromString("rw-r-----"))

        assertThatThrownBy {
            PreS5VertexServiceAccountCredentialProvider(RagV2VertexProperties(localRoot = root.toString())).acquire()
        }.isInstanceOf(PreS5VertexServiceAccountCredentialException::class.java)
    }

    private fun credentialJson(): String {
        val keyPair = KeyPairGenerator.getInstance("RSA").apply { initialize(2048) }.generateKeyPair()
        val encoded = Base64.getMimeEncoder(64, "\n".toByteArray()).encodeToString(keyPair.private.encoded)
        val pem = "-----BEGIN PRIVATE KEY-----\n$encoded\n-----END PRIVATE KEY-----\n"
        return JsonMapper.builder().build().writeValueAsString(
            linkedMapOf(
                "type" to "service_account",
                "project_id" to "project-test-123",
                "private_key_id" to "a".repeat(40),
                "private_key" to pem,
                "client_email" to "vertex-test@project-test-123.iam.gserviceaccount.com",
                "client_id" to "123456789012345678901",
                "auth_uri" to "https://accounts.google.com/o/oauth2/auth",
                "token_uri" to "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url" to "https://www.googleapis.com/oauth2/v1/certs",
                "client_x509_cert_url" to
                    "https://www.googleapis.com/robot/v1/metadata/x509/" +
                    "vertex-test%40project-test-123.iam.gserviceaccount.com",
                "universe_domain" to "googleapis.com",
            ),
        )
    }

    private fun secureDirectory(path: Path) {
        Files.setPosixFilePermissions(path, DIRECTORY_PERMISSIONS)
    }

    private companion object {
        val DIRECTORY_PERMISSIONS: Set<java.nio.file.attribute.PosixFilePermission> = PosixFilePermissions.fromString("rwx------")
        val FILE_PERMISSIONS: Set<java.nio.file.attribute.PosixFilePermission> = PosixFilePermissions.fromString("rw-------")
    }
}
