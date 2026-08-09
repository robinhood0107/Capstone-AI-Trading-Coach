package com.capstone.decision.infrastructure.vertex

import com.google.auth.oauth2.ServiceAccountCredentials
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import tools.jackson.core.JacksonException
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.JsonNode
import tools.jackson.databind.json.JsonMapper
import java.io.ByteArrayInputStream
import java.nio.ByteBuffer
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.Path
import java.nio.file.SecureDirectoryStream
import java.nio.file.StandardOpenOption
import java.nio.file.attribute.PosixFileAttributeView
import java.nio.file.attribute.PosixFileAttributes
import java.nio.file.attribute.PosixFilePermission
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.util.Base64

/**
 * Developer API key와 ambient user credential을 열지 않는다. `GOOGLE_APPLICATION_CREDENTIALS`의
 * secure local service-account file만 ADC source로 읽고, token request도 fixed no-retry transport로 분리한다.
 */
@Component
@ConditionalOnProperty(name = ["app.rag-v2.vertex.enabled"], havingValue = "true")
internal class PreS5VertexCredentialProvider(
    private val tokenExecutor: PreS5VertexTokenExecutor,
) {
    private var environment: (String) -> String? = { name -> System.getenv(name) }
    private var clock: Clock = Clock.systemUTC()

    fun prepare(activation: PreS5VertexActivation): PreS5VertexPreparedCredential {
        try {
            val configuredProject = requireText(environment("GOOGLE_CLOUD_PROJECT"))
            require(configuredProject == activation.projectId)
            val path = Path.of(requireText(environment("GOOGLE_APPLICATION_CREDENTIALS")))
            require(path.isAbsolute && path.normalize() == path)
            val root = requireNotNull(path.parent)
            val rootAttributes = requireDirectory(root)
            val bytes = readRegularSingleLinkFile(root, path.fileName, rootAttributes.owner())
            try {
                val rootAfter = requireDirectory(root)
                require(rootAttributes.fileKey() != null && rootAttributes.fileKey() == rootAfter.fileKey())
                validateServiceAccount(bytes, activation.projectId)
                // library default retry is explicitly disabled even though token exchange uses our bounded executor.
                val credentials =
                    ServiceAccountCredentials
                        .fromStream(ByteArrayInputStream(bytes))
                        .createWithCustomRetryStrategy(false)
                return PreS5VertexPreparedCredential(credentials, tokenExecutor, clock)
            } finally {
                bytes.fill(0)
            }
        } catch (error: PreS5VertexCredentialException) {
            throw error
        } catch (_: Exception) {
            throw PreS5VertexCredentialException()
        }
    }

    private fun validateServiceAccount(
        bytes: ByteArray,
        expectedProjectId: String,
    ) {
        try {
            val root = CREDENTIAL_MAPPER.readTree(bytes)
            require(root != null && root.isObject)
            require(
                root
                    .properties()
                    .map { it.key }
                    .toSet()
                    .all { it in ALLOWED_FIELDS },
            )
            require(
                root
                    .properties()
                    .map { it.key }
                    .toSet()
                    .containsAll(REQUIRED_FIELDS),
            )
            require(text(root, "type") == "service_account")
            require(text(root, "project_id") == expectedProjectId)
            require(PROJECT_ID.matches(text(root, "project_id")))
            require(PRIVATE_KEY_ID.matches(text(root, "private_key_id")))
            val privateKey = text(root, "private_key")
            require(privateKey.startsWith("-----BEGIN PRIVATE KEY-----\n"))
            require(privateKey.endsWith("-----END PRIVATE KEY-----\n"))
            require(CLIENT_EMAIL.matches(text(root, "client_email")))
            require(CLIENT_ID.matches(text(root, "client_id")))
            require(text(root, "token_uri") == TOKEN_URI)
            root.properties().forEach { (_, value) -> require(value.isString) }
        } catch (_: JacksonException) {
            throw PreS5VertexCredentialException()
        } catch (_: IllegalArgumentException) {
            throw PreS5VertexCredentialException()
        } catch (_: IllegalStateException) {
            throw PreS5VertexCredentialException()
        }
    }

    private fun text(
        root: JsonNode,
        field: String,
    ): String =
        root
            .get(field)
            ?.takeIf { it.isString }
            ?.stringValue()
            ?.takeIf { it.isNotBlank() && it.length <= MAX_CREDENTIAL_BYTES }
            ?: throw PreS5VertexCredentialException()

    private fun requireText(value: String?): String =
        value
            ?.takeIf { it.isNotBlank() && it.length <= 512 && '\u0000' !in it && '\n' !in it && '\r' !in it }
            ?: throw PreS5VertexCredentialException()

    private fun requireDirectory(path: Path): PosixFileAttributes {
        val attributes = Files.readAttributes(path, PosixFileAttributes::class.java, LinkOption.NOFOLLOW_LINKS)
        require(attributes.isDirectory && !attributes.isSymbolicLink)
        require(attributes.permissions() == DIRECTORY_PERMISSIONS)
        val processOwner =
            path.fileSystem.userPrincipalLookupService
                .lookupPrincipalByName(System.getProperty("user.name"))
        require(attributes.owner() == processOwner)
        return attributes
    }

    private fun readRegularSingleLinkFile(
        directory: Path,
        fileName: Path,
        expectedOwner: java.nio.file.attribute.UserPrincipal,
    ): ByteArray {
        val filePath = directory.resolve(fileName).normalize()
        require(filePath.parent == directory)
        Files.newDirectoryStream(directory).use { directoryStream ->
            require(directoryStream is SecureDirectoryStream<Path>)
            val view =
                requireNotNull(
                    directoryStream.getFileAttributeView(
                        fileName,
                        PosixFileAttributeView::class.java,
                        LinkOption.NOFOLLOW_LINKS,
                    ),
                )
            val before = view.readAttributes()
            require(before.isRegularFile && !before.isSymbolicLink)
            require(before.permissions() == FILE_PERMISSIONS)
            require(before.owner() == expectedOwner)
            require(before.size() in 1..MAX_CREDENTIAL_BYTES.toLong())
            require(linkCount(filePath) == 1L)
            val bytes =
                directoryStream
                    .newByteChannel(fileName, setOf(StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS))
                    .use { channel ->
                        val buffer = ByteBuffer.allocate(MAX_CREDENTIAL_BYTES + 1)
                        while (buffer.hasRemaining() && channel.read(buffer) != -1) {
                            // descriptor-level bounded read로 credential 교체와 확장을 fail-closed 한다.
                        }
                        require(buffer.position() <= MAX_CREDENTIAL_BYTES)
                        buffer.flip()
                        ByteArray(buffer.remaining()).also(buffer::get)
                    }
            val after = view.readAttributes()
            require(before.fileKey() != null && before.fileKey() == after.fileKey())
            require(before.size() == after.size() && bytes.size.toLong() == before.size())
            require(linkCount(filePath) == 1L)
            return bytes
        }
    }

    private fun linkCount(path: Path): Long = (Files.getAttribute(path, "unix:nlink", LinkOption.NOFOLLOW_LINKS) as Number).toLong()

    internal companion object {
        fun forTest(
            tokenExecutor: PreS5VertexTokenExecutor,
            environment: (String) -> String?,
            clock: Clock,
        ): PreS5VertexCredentialProvider =
            PreS5VertexCredentialProvider(tokenExecutor).apply {
                this.environment = environment
                this.clock = clock
            }

        val CREDENTIAL_MAPPER =
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
                                .maxNumberLength(32)
                                .maxStringLength(MAX_CREDENTIAL_BYTES)
                                .maxNameLength(64)
                                .build(),
                        ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                        .build(),
                ).build()
        const val TOKEN_URI = "https://oauth2.googleapis.com/token"
        const val CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
        val REQUIRED_FIELDS = setOf("type", "project_id", "private_key_id", "private_key", "client_email", "client_id", "token_uri")
        val ALLOWED_FIELDS =
            REQUIRED_FIELDS +
                setOf(
                    "auth_uri",
                    "auth_provider_x509_cert_url",
                    "client_x509_cert_url",
                    "universe_domain",
                )
        val DIRECTORY_PERMISSIONS =
            setOf(
                PosixFilePermission.OWNER_READ,
                PosixFilePermission.OWNER_WRITE,
                PosixFilePermission.OWNER_EXECUTE,
            )
        val FILE_PERMISSIONS =
            setOf(
                PosixFilePermission.OWNER_READ,
                PosixFilePermission.OWNER_WRITE,
            )
        val PROJECT_ID = Regex("^[a-z][a-z0-9-]{4,62}$")
        val PRIVATE_KEY_ID = Regex("^[0-9a-f]{40}$")
        val CLIENT_EMAIL = Regex("^[a-z0-9][a-z0-9-]{0,128}@[a-z0-9-]+\\.iam\\.gserviceaccount\\.com$")
        val CLIENT_ID = Regex("^[0-9]{6,64}$")
        const val MAX_CREDENTIAL_BYTES = 16_384
    }
}

/**
 * access token은 token-attempt lease 뒤 한 번만 메모리에 만든다. Google auth library의 default refresh
 * transport를 쓰지 않아 redirect, proxy, automatic retry, raw OAuth response logging 경로를 열지 않는다.
 */
internal class PreS5VertexPreparedCredential internal constructor(
    private val credentials: ServiceAccountCredentials,
    private val tokenExecutor: PreS5VertexTokenExecutor,
    private val clock: Clock,
) {
    fun issueAccessToken(
        attempt: PreS5VertexTokenAttempt,
        timeout: Duration,
        expiresAt: Instant,
    ): String {
        val assertion = signedAssertion()
        try {
            return try {
                val response =
                    tokenExecutor.execute(
                        PreS5VertexTokenRequest(
                            assertion = assertion,
                            timeout = timeout,
                            expiresAt = expiresAt,
                            attempt = attempt,
                        ),
                    )
                try {
                    require(response.statusCode in 200..299)
                    parseAccessToken(response.body)
                } finally {
                    response.body.fill(0)
                }
            } catch (error: PreS5VertexCredentialException) {
                throw error
            } catch (_: Exception) {
                throw PreS5VertexCredentialException()
            }
        } finally {
            assertion.fill(0)
        }
    }

    private fun signedAssertion(): ByteArray {
        val issuedAt = Instant.now(clock).epochSecond
        val header = TOKEN_MAPPER.writeValueAsBytes(linkedMapOf("alg" to "RS256", "typ" to "JWT", "kid" to credentials.privateKeyId))
        val claims =
            TOKEN_MAPPER.writeValueAsBytes(
                linkedMapOf(
                    "iss" to credentials.clientEmail,
                    "scope" to PreS5VertexCredentialProvider.CLOUD_PLATFORM_SCOPE,
                    "aud" to PreS5VertexCredentialProvider.TOKEN_URI,
                    "iat" to issuedAt,
                    "exp" to issuedAt + TOKEN_LIFETIME_SECONDS,
                ),
            )
        val encodedHeader = base64Url(header)
        val encodedClaims = base64Url(claims)
        val signingInput = "$encodedHeader.$encodedClaims".toByteArray(StandardCharsets.US_ASCII)
        val signature =
            try {
                credentials.sign(signingInput)
            } catch (_: Exception) {
                throw PreS5VertexCredentialException()
            }
        return try {
            "$encodedHeader.$encodedClaims.${base64Url(signature)}".toByteArray(StandardCharsets.US_ASCII)
        } finally {
            header.fill(0)
            claims.fill(0)
            signingInput.fill(0)
            signature.fill(0)
        }
    }

    private fun parseAccessToken(body: ByteArray): String {
        try {
            val root = TOKEN_MAPPER.readTree(body)
            require(root != null && root.isObject)
            val fields = root.properties().map { it.key }.toSet()
            require(fields.all { it in TOKEN_RESPONSE_FIELDS })
            require(fields.containsAll(REQUIRED_TOKEN_RESPONSE_FIELDS))
            require(root.get("token_type")?.stringValue() == "Bearer")
            val expiresIn = root.get("expires_in")?.takeIf { it.isInt || it.isLong }?.longValue()
            require(expiresIn != null && expiresIn in 1..3_600)
            return root
                .get("access_token")
                ?.takeIf { it.isString }
                ?.stringValue()
                ?.takeIf { it.isNotBlank() && it.length <= 16_384 && '\u0000' !in it && '\n' !in it && '\r' !in it }
                ?: throw PreS5VertexCredentialException()
        } catch (error: PreS5VertexCredentialException) {
            throw error
        } catch (_: Exception) {
            throw PreS5VertexCredentialException()
        }
    }

    private fun base64Url(bytes: ByteArray): String = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)

    private companion object {
        val TOKEN_MAPPER =
            JsonMapper
                .builder(
                    JsonFactory
                        .builder()
                        .streamReadConstraints(
                            StreamReadConstraints
                                .builder()
                                .maxNestingDepth(2)
                                .maxDocumentLength(16_384)
                                .maxTokenCount(32)
                                .maxNumberLength(16)
                                .maxStringLength(16_384)
                                .maxNameLength(64)
                                .build(),
                        ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                        .build(),
                ).build()
        val TOKEN_RESPONSE_FIELDS = setOf("access_token", "expires_in", "token_type", "scope")
        val REQUIRED_TOKEN_RESPONSE_FIELDS = setOf("access_token", "expires_in", "token_type")
        const val TOKEN_LIFETIME_SECONDS = 300L
    }
}

internal class PreS5VertexCredentialException : RuntimeException()
