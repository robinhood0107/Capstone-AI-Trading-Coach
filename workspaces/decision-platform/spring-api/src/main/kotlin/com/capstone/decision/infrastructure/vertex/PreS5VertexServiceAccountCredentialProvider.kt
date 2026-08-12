package com.capstone.decision.infrastructure.vertex

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.json.JsonMapper
import java.nio.ByteBuffer
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.Path
import java.nio.file.SecureDirectoryStream
import java.nio.file.StandardOpenOption
import java.nio.file.attribute.PosixFileAttributeView
import java.nio.file.attribute.PosixFileAttributes
import java.nio.file.attribute.PosixFilePermission
import java.security.KeyFactory
import java.security.PrivateKey
import java.security.spec.PKCS8EncodedKeySpec
import java.util.Base64

internal data class PreS5VertexServiceAccountCredential(
    val projectId: String,
    val clientEmail: String,
    val privateKeyId: String,
    val privateKey: PrivateKey,
)

/**
 * 사용자가 지정한 service-account JSON 한 개만 owner-only local root에서 읽는다. ADC 검색, 환경변수
 * fallback, symlink와 임의 credential path를 허용하지 않아 다른 Google 자격증명이 승격되지 않는다.
 */
@Component
@ConditionalOnProperty(name = ["app.rag-v2.vertex.enabled"], havingValue = "true")
internal class PreS5VertexServiceAccountCredentialProvider(
    private val properties: RagV2VertexProperties,
) {
    private val mapper =
        JsonMapper
            .builder(
                JsonFactory
                    .builder()
                    .streamReadConstraints(
                        StreamReadConstraints
                            .builder()
                            .maxNestingDepth(2)
                            .maxDocumentLength(MAX_CREDENTIAL_BYTES.toLong())
                            .maxTokenCount(64)
                            .maxStringLength(MAX_CREDENTIAL_BYTES)
                            .maxNameLength(64)
                            .build(),
                    ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                    .build(),
            ).build()

    fun acquire(): PreS5VertexServiceAccountCredential {
        var raw: ByteArray? = null
        var keyBytes: ByteArray? = null
        try {
            val root = Path.of(properties.localRoot)
            val rootAttributes = requireDirectory(root, null)
            val secrets = root.resolve(SECRETS_DIRECTORY).normalize()
            require(secrets.parent == root)
            val secretsAttributes = requireDirectory(secrets, rootAttributes.owner())
            raw = readSingleLinkFile(secrets, CREDENTIAL_FILE, secretsAttributes.owner())
            val document = mapper.readTree(raw)
            require(document != null && document.isObject)
            require(document.properties().map { it.key }.toSet() == REQUIRED_FIELDS)
            require(document["type"]?.stringValue() == "service_account")
            require(document["token_uri"]?.stringValue() == TOKEN_URI)
            require(document["universe_domain"]?.stringValue() == "googleapis.com")
            val projectId = requireNotNull(document["project_id"]?.stringValue()).also { require(PROJECT_ID.matches(it)) }
            val clientEmail = requireNotNull(document["client_email"]?.stringValue()).also { require(CLIENT_EMAIL.matches(it)) }
            val privateKeyId = requireNotNull(document["private_key_id"]?.stringValue()).also { require(KEY_ID.matches(it)) }
            val privateKeyPem = requireNotNull(document["private_key"]?.stringValue())
            require(privateKeyPem.startsWith(PEM_BEGIN) && privateKeyPem.endsWith(PEM_END))
            keyBytes =
                Base64.getMimeDecoder().decode(
                    privateKeyPem.removePrefix(PEM_BEGIN).removeSuffix(PEM_END),
                )
            require(keyBytes.size in MINIMUM_KEY_BYTES..MAXIMUM_KEY_BYTES)
            val privateKey = KeyFactory.getInstance("RSA").generatePrivate(PKCS8EncodedKeySpec(keyBytes))
            return PreS5VertexServiceAccountCredential(projectId, clientEmail, privateKeyId, privateKey)
        } catch (_: Exception) {
            throw PreS5VertexServiceAccountCredentialException()
        } finally {
            raw?.fill(0)
            keyBytes?.fill(0)
        }
    }

    private fun requireDirectory(
        path: Path,
        owner: java.nio.file.attribute.UserPrincipal?,
    ): PosixFileAttributes {
        require(path.isAbsolute && path.normalize() == path)
        val attributes = Files.readAttributes(path, PosixFileAttributes::class.java, LinkOption.NOFOLLOW_LINKS)
        require(attributes.isDirectory && !attributes.isSymbolicLink && attributes.permissions() == DIRECTORY_PERMISSIONS)
        val processOwner = path.fileSystem.userPrincipalLookupService.lookupPrincipalByName(System.getProperty("user.name"))
        require(attributes.owner() == processOwner && (owner == null || attributes.owner() == owner))
        return attributes
    }

    private fun readSingleLinkFile(
        directory: Path,
        fileName: Path,
        owner: java.nio.file.attribute.UserPrincipal,
    ): ByteArray {
        val path = directory.resolve(fileName).normalize()
        require(path.parent == directory)
        Files.newDirectoryStream(directory).use { stream ->
            require(stream is SecureDirectoryStream<Path>)
            val view = requireNotNull(stream.getFileAttributeView(fileName, PosixFileAttributeView::class.java, LinkOption.NOFOLLOW_LINKS))
            val before = view.readAttributes()
            require(before.isRegularFile && !before.isSymbolicLink)
            require(before.owner() == owner && before.permissions() == FILE_PERMISSIONS)
            require(before.size() in 1..MAX_CREDENTIAL_BYTES.toLong() && linkCount(path) == 1L)
            val bytes =
                stream.newByteChannel(fileName, setOf(StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS)).use { channel ->
                    val buffer = ByteBuffer.allocate(MAX_CREDENTIAL_BYTES + 1)
                    while (buffer.hasRemaining() && channel.read(buffer) != -1) {
                        // descriptor-bound bounded read prevents replacement and expansion races.
                    }
                    require(buffer.position() <= MAX_CREDENTIAL_BYTES)
                    buffer.flip()
                    ByteArray(buffer.remaining()).also(buffer::get)
                }
            val after = view.readAttributes()
            require(before.fileKey() != null && before.fileKey() == after.fileKey())
            require(before.size() == after.size() && before.size() == bytes.size.toLong() && linkCount(path) == 1L)
            return bytes
        }
    }

    private fun linkCount(path: Path): Long = (Files.getAttribute(path, "unix:nlink", LinkOption.NOFOLLOW_LINKS) as Number).toLong()

    private companion object {
        const val SECRETS_DIRECTORY = "secrets"
        val CREDENTIAL_FILE: Path = Path.of("pre-s5-vertex-service-account.json")
        const val TOKEN_URI = "https://oauth2.googleapis.com/token"
        const val PEM_BEGIN = "-----BEGIN PRIVATE KEY-----\n"
        const val PEM_END = "\n-----END PRIVATE KEY-----\n"
        const val MAX_CREDENTIAL_BYTES = 32 * 1024
        const val MINIMUM_KEY_BYTES = 1_000
        const val MAXIMUM_KEY_BYTES = 16 * 1024
        val PROJECT_ID = Regex("^[a-z][a-z0-9-]{4,62}[a-z0-9]$")
        val CLIENT_EMAIL = Regex("^[A-Za-z0-9._%+-]{1,128}@[A-Za-z0-9.-]{1,190}\\.iam\\.gserviceaccount\\.com$")
        val KEY_ID = Regex("^[0-9a-f]{16,128}$")
        val REQUIRED_FIELDS =
            setOf(
                "type",
                "project_id",
                "private_key_id",
                "private_key",
                "client_email",
                "client_id",
                "auth_uri",
                "token_uri",
                "auth_provider_x509_cert_url",
                "client_x509_cert_url",
                "universe_domain",
            )
        val DIRECTORY_PERMISSIONS =
            setOf(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE, PosixFilePermission.OWNER_EXECUTE)
        val FILE_PERMISSIONS = setOf(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE)
    }
}

internal class PreS5VertexServiceAccountCredentialException : RuntimeException()
