package com.capstone.decision.infrastructure.vertex

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import tools.jackson.core.JacksonException
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.JsonNode
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
import java.security.MessageDigest
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime
import java.util.TreeMap

internal data class PreS5VertexActivation(
    val packetSha256: String,
    val nonceSha256: String,
    val authenticationMode: String,
    val requestId: String,
    val scopeClaimId: String,
    val questionFingerprintHmac: String,
    val answerMode: String,
    val consentEventId: String,
    val policySha256: String,
    val processorSetSha256: String,
    val expiresAt: Instant,
    val inputTokenCap: Int,
    val outputTokenCap: Int,
    val inputByteCap: Int,
    val costCapMicrousd: Long,
    val inputMicrousdPerToken: Long,
    val outputMicrousdPerToken: Long,
    val tokenPhysicalCallCap: Int,
    val generateContentPhysicalCallCap: Int,
)

/**
 * local-only five-minute packet은 provider socket 생성 전에 exact source control plane에서 다시 읽는다.
 * nonce와 evidence 원문은 projection에 노출하지 않고 SHA-256 receipt만 DB lease에 전달한다.
 */
@Component
@ConditionalOnProperty(name = ["app.rag-v2.vertex.enabled"], havingValue = "true")
internal class PreS5VertexActivationReader(
    private val properties: RagV2VertexProperties,
    private val clock: Clock = Clock.systemUTC(),
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
                            .maxDocumentLength(MAX_PACKET_BYTES.toLong())
                            .maxTokenCount(96)
                            .maxNumberLength(20)
                            .maxStringLength(512)
                            .maxNameLength(64)
                            .build(),
                    ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                    .build(),
            ).build()

    fun read(): PreS5VertexActivation {
        try {
            properties.validateEnabled()
            val root = Path.of(properties.localRoot)
            val rootMetadata = requireDirectory(root, expectedOwner = null)
            val control = root.resolve(CONTROL_DIRECTORY).normalize()
            require(control.parent == root)
            val controlMetadata = requireDirectory(control, expectedOwner = rootMetadata.owner())
            val packet = readRegularSingleLinkFile(control, PACKET_FILE, controlMetadata.owner())
            try {
                val rootAfter = requireDirectory(root, expectedOwner = rootMetadata.owner())
                val controlAfter = requireDirectory(control, expectedOwner = controlMetadata.owner())
                require(rootMetadata.fileKey() != null && rootMetadata.fileKey() == rootAfter.fileKey())
                require(controlMetadata.fileKey() != null && controlMetadata.fileKey() == controlAfter.fileKey())
                return parsePacket(packet)
            } finally {
                packet.fill(0)
            }
        } catch (error: PreS5VertexActivationException) {
            throw error
        } catch (_: Exception) {
            throw PreS5VertexActivationException("PRE_S5_VERTEX_PACKET_BOUNDARY")
        }
    }

    private fun parsePacket(raw: ByteArray): PreS5VertexActivation {
        try {
            val root = mapper.readTree(raw)
            require(root != null && root.isObject)
            require(root.properties().map { it.key }.toSet() == PACKET_FIELDS)
            val values = TreeMap<String, Any>()
            PACKET_FIELDS.forEach { field -> values[field] = scalar(root, field) }

            val now = Instant.now(clock)
            val issuedAt = parseInstant(text(values, "issuedAt"))
            val expiresAt = parseInstant(text(values, "expiresAt"))
            require(issuedAt < expiresAt)
            require(Duration.between(issuedAt, expiresAt) <= Duration.ofMinutes(5))
            require(!now.isBefore(issuedAt) && now.isBefore(expiresAt))
            require(text(values, "contractId") == "pre-s5-vertex-activation/v2")
            require(text(values, "provider") == "VERTEX_AI")
            require(text(values, "authenticationMode") == AUTHENTICATION_MODE)
            require(text(values, "origin") == ORIGIN)
            require(text(values, "endpoint") == ENDPOINT)
            require(text(values, "authOrigin") == AUTH_ORIGIN)
            require(text(values, "authEndpoint") == AUTH_ENDPOINT)
            require(text(values, "modelId") == MODEL_ID)
            require(REQUEST_ID.matches(text(values, "requestId")))
            require(SCOPE_CLAIM_ID.matches(text(values, "scopeClaimId")))
            require(text(values, "answerMode") in ANSWER_MODES)
            require(CONSENT_EVENT_ID.matches(text(values, "consentEventId")))
            require(text(values, "headCommit") == properties.headCommit)
            require(text(values, "treeDigest") == properties.treeDigest)
            require(text(values, "ciDigest") == properties.ciDigest)
            require(text(values, "securityDigest") == properties.securityDigest)
            HASH_FIELDS.forEach { field -> require(SHA256.matches(text(values, field))) }
            require(OPERATOR.matches(text(values, "operator")))
            require(NONCE.matches(text(values, "nonce")))
            val logicalCallCap = integer(values, "logicalCallCap", 1, 1)
            val physicalCallCap = integer(values, "physicalCallCap", 1, 1)
            val tokenPhysicalCallCap = integer(values, "tokenPhysicalCallCap", 0, 0)
            val generateContentPhysicalCallCap = integer(values, "generateContentPhysicalCallCap", 1, 1)
            require(
                logicalCallCap == 1 &&
                    physicalCallCap == tokenPhysicalCallCap + generateContentPhysicalCallCap,
            )
            require(integer(values, "retryCount", 0, 0) == 0)
            require(integer(values, "rawArtifactCount", 0, 0) == 0)
            val inputTokenCap = integer(values, "inputTokenCap", 1, 120_000)
            val outputTokenCap = integer(values, "outputTokenCap", 1, 32_768)
            val inputByteCap = integer(values, "inputByteCap", 1, 60_000)
            val costCapMicrousd = long(values, "costCapMicrousd", 1, 1_000_000_000)
            val inputMicrousdPerToken = long(values, "inputMicrousdPerToken", 1, 1_000_000)
            val outputMicrousdPerToken = long(values, "outputMicrousdPerToken", 1, 1_000_000)
            // countTokens 추가 호출 없이 byte-bound와 fixed framing margin으로 approved input cost를 선차단한다.
            require(inputByteCap + INPUT_TOKEN_SAFETY_MARGIN <= inputTokenCap)
            require(
                inputTokenCap.toLong() * inputMicrousdPerToken +
                    outputTokenCap.toLong() * outputMicrousdPerToken <= costCapMicrousd,
            )
            val canonical = mapper.writeValueAsBytes(values)
            val packetSha256 = sha256(canonical)
            canonical.fill(0)
            return PreS5VertexActivation(
                packetSha256 = packetSha256,
                nonceSha256 = sha256(text(values, "nonce").toByteArray(Charsets.UTF_8)),
                authenticationMode = text(values, "authenticationMode"),
                requestId = text(values, "requestId"),
                scopeClaimId = text(values, "scopeClaimId"),
                questionFingerprintHmac = text(values, "questionFingerprintHmac"),
                answerMode = text(values, "answerMode"),
                consentEventId = text(values, "consentEventId"),
                policySha256 = text(values, "policySha256"),
                processorSetSha256 = text(values, "processorSetSha256"),
                expiresAt = expiresAt,
                inputTokenCap = inputTokenCap,
                outputTokenCap = outputTokenCap,
                inputByteCap = inputByteCap,
                costCapMicrousd = costCapMicrousd,
                inputMicrousdPerToken = inputMicrousdPerToken,
                outputMicrousdPerToken = outputMicrousdPerToken,
                tokenPhysicalCallCap = tokenPhysicalCallCap,
                generateContentPhysicalCallCap = generateContentPhysicalCallCap,
            )
        } catch (_: JacksonException) {
            throw PreS5VertexActivationException("PRE_S5_VERTEX_PACKET_INVALID")
        } catch (_: IllegalArgumentException) {
            throw PreS5VertexActivationException("PRE_S5_VERTEX_PACKET_INVALID")
        } catch (_: IllegalStateException) {
            throw PreS5VertexActivationException("PRE_S5_VERTEX_PACKET_INVALID")
        }
    }

    private fun scalar(
        root: JsonNode,
        field: String,
    ): Any {
        val node = root.get(field) ?: throw PreS5VertexActivationException("PRE_S5_VERTEX_PACKET_INVALID")
        return when {
            node.isString -> node.stringValue()
            node.isInt -> node.intValue()
            node.isLong -> node.longValue()
            else -> throw PreS5VertexActivationException("PRE_S5_VERTEX_PACKET_INVALID")
        }
    }

    private fun text(
        values: Map<String, Any>,
        field: String,
    ): String = values[field] as? String ?: throw PreS5VertexActivationException("PRE_S5_VERTEX_PACKET_INVALID")

    private fun integer(
        values: Map<String, Any>,
        field: String,
        minimum: Int,
        maximum: Int,
    ): Int =
        (values[field] as? Int)
            ?.takeIf { it in minimum..maximum }
            ?: throw PreS5VertexActivationException("PRE_S5_VERTEX_PACKET_INVALID")

    private fun long(
        values: Map<String, Any>,
        field: String,
        minimum: Long,
        maximum: Long,
    ): Long =
        when (val value = values[field]) {
            is Int -> value.toLong()
            is Long -> value
            else -> null
        }?.takeIf { it in minimum..maximum }
            ?: throw PreS5VertexActivationException("PRE_S5_VERTEX_PACKET_INVALID")

    private fun parseInstant(value: String): Instant = OffsetDateTime.parse(value).toInstant()

    private fun requireDirectory(
        path: Path,
        expectedOwner: java.nio.file.attribute.UserPrincipal?,
    ): PosixFileAttributes {
        require(path.isAbsolute && path.normalize() == path)
        val attributes = Files.readAttributes(path, PosixFileAttributes::class.java, LinkOption.NOFOLLOW_LINKS)
        require(attributes.isDirectory && !attributes.isSymbolicLink)
        require(attributes.permissions() == DIRECTORY_PERMISSIONS)
        val processOwner =
            path.fileSystem.userPrincipalLookupService
                .lookupPrincipalByName(System.getProperty("user.name"))
        require(attributes.owner() == processOwner)
        if (expectedOwner != null) {
            require(attributes.owner() == expectedOwner)
        }
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
            require(before.size() in 1..MAX_PACKET_BYTES.toLong())
            require(linkCount(filePath) == 1L)
            val bytes =
                directoryStream
                    .newByteChannel(fileName, setOf(StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS))
                    .use { channel ->
                        val buffer = ByteBuffer.allocate(MAX_PACKET_BYTES + 1)
                        while (buffer.hasRemaining() && channel.read(buffer) != -1) {
                            // descriptor 단위 bounded read로 packet 교체와 확장을 fail-closed 한다.
                        }
                        require(buffer.position() <= MAX_PACKET_BYTES)
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

    private fun sha256(value: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(value).joinToString("") { "%02x".format(it) }

    private companion object {
        const val CONTROL_DIRECTORY = "control"
        val PACKET_FILE: Path = Path.of("pre-s5-vertex-activation.json")
        const val ORIGIN = "https://aiplatform.googleapis.com"
        const val ENDPOINT =
            "POST /v1/publishers/google/models/gemini-3.5-flash:generateContent?key={VERTEX_API_KEY}"
        const val AUTH_ORIGIN = "https://aiplatform.googleapis.com"
        const val AUTH_ENDPOINT = "QUERY_PARAMETER:key"
        const val AUTHENTICATION_MODE = "VERTEX_EXPRESS_API_KEY"
        const val MODEL_ID = "gemini-3.5-flash"
        val PACKET_FIELDS =
            setOf(
                "contractId",
                "provider",
                "authenticationMode",
                "origin",
                "endpoint",
                "authOrigin",
                "authEndpoint",
                "requestId",
                "scopeClaimId",
                "questionFingerprintHmac",
                "answerMode",
                "consentEventId",
                "modelId",
                "headCommit",
                "treeDigest",
                "ciDigest",
                "securityDigest",
                "apiKeySecurityEvidenceSha256",
                "dataGovernanceStateEvidenceSha256",
                "abuseMonitoringStateEvidenceSha256",
                "modelAvailabilityEvidenceSha256",
                "policySha256",
                "processorSetSha256",
                "issuedAt",
                "expiresAt",
                "logicalCallCap",
                "physicalCallCap",
                "tokenPhysicalCallCap",
                "generateContentPhysicalCallCap",
                "inputTokenCap",
                "outputTokenCap",
                "inputByteCap",
                "costCapMicrousd",
                "inputMicrousdPerToken",
                "outputMicrousdPerToken",
                "retryCount",
                "rawArtifactCount",
                "operator",
                "nonce",
            )
        val HASH_FIELDS =
            setOf(
                "treeDigest",
                "ciDigest",
                "securityDigest",
                "apiKeySecurityEvidenceSha256",
                "dataGovernanceStateEvidenceSha256",
                "abuseMonitoringStateEvidenceSha256",
                "modelAvailabilityEvidenceSha256",
                "policySha256",
                "processorSetSha256",
                "questionFingerprintHmac",
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
        val SHA256 = Regex("^[0-9a-f]{64}$")
        val REQUEST_ID = Regex("^req_[A-Za-z0-9_-]{12,96}$")
        val SCOPE_CLAIM_ID = Regex("^rvs_[0-9a-f]{32}$")
        val CONSENT_EVENT_ID = Regex("^rce_[A-Za-z0-9_-]{12,96}$")
        val ANSWER_MODES = setOf("CONCISE", "DETAILED")
        val OPERATOR = Regex("^[A-Za-z0-9._@-]{1,96}$")
        val NONCE = Regex("^[A-Za-z0-9_-]{16,128}$")
        const val MAX_PACKET_BYTES = 16_384
        const val INPUT_TOKEN_SAFETY_MARGIN = 512
    }
}

internal class PreS5VertexActivationException(
    val code: String,
) : RuntimeException()
