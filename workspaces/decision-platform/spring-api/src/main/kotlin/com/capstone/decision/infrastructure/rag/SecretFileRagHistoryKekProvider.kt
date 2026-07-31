package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagHistoryCorruptedException
import org.springframework.stereotype.Component
import java.nio.ByteBuffer
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
import java.util.HexFormat

@Component
class SecretFileRagHistoryKekProvider(
    private val properties: RagGuardHistoryProperties,
    private val clock: Clock = Clock.systemUTC(),
) : RagHistoryKekProvider {
    override fun current(): RagHistoryKek = load(properties.currentKekVersion, oldKey = false)

    override fun byVersion(version: String): RagHistoryKek =
        load(
            version = version,
            oldKey = version != properties.currentKekVersion,
        )

    /**
     * KEK 파일은 absolute 0700 directory 아래 exact 0600 regular single-link file만 허용한다.
     */
    private fun load(
        version: String,
        oldKey: Boolean,
    ): RagHistoryKek {
        try {
            require(RagGuardHistoryProperties.KEK_VERSION.matches(version))
            val directory = Path.of(properties.historySecretDirectory)
            require(directory.isAbsolute)
            val normalizedDirectory = directory.normalize()
            require(normalizedDirectory == directory)
            val directoryAttributes =
                Files.readAttributes(
                    normalizedDirectory,
                    PosixFileAttributes::class.java,
                    LinkOption.NOFOLLOW_LINKS,
                )
            require(directoryAttributes.isDirectory && !directoryAttributes.isSymbolicLink)
            require(directoryAttributes.permissions() == DIRECTORY_PERMISSIONS)
            val processOwner =
                normalizedDirectory.fileSystem.userPrincipalLookupService
                    .lookupPrincipalByName(System.getProperty("user.name"))
            require(directoryAttributes.owner() == processOwner)

            val keyName = Path.of("rag-history-$version.key")
            val keyPath = normalizedDirectory.resolve(keyName).normalize()
            require(keyPath.parent == normalizedDirectory)
            val encoded =
                Files.newDirectoryStream(normalizedDirectory).use { directoryStream ->
                    require(directoryStream is SecureDirectoryStream<Path>)
                    readSecureKey(
                        directoryStream = directoryStream,
                        keyName = keyName,
                        keyPath = keyPath,
                        expectedOwner = directoryAttributes.owner(),
                        oldKey = oldKey,
                    )
                }
            val decoded =
                if (HEX_KEY.matches(encoded)) {
                    HexFormat.of().parseHex(encoded)
                } else {
                    Base64.getUrlDecoder().decode(padBase64(encoded))
                }
            require(decoded.size == 32)
            return RagHistoryKek(version, decoded)
        } catch (_: Exception) {
            throw RagHistoryCorruptedException()
        }
    }

    private fun readSecureKey(
        directoryStream: SecureDirectoryStream<Path>,
        keyName: Path,
        keyPath: Path,
        expectedOwner: java.nio.file.attribute.UserPrincipal,
        oldKey: Boolean,
    ): String {
        val view =
            requireNotNull(
                directoryStream.getFileAttributeView(
                    keyName,
                    PosixFileAttributeView::class.java,
                    LinkOption.NOFOLLOW_LINKS,
                ),
            )
        val before = view.readAttributes()
        validateKeyAttributes(before, expectedOwner, oldKey)
        require(linkCount(keyPath) == 1L)
        val bytes =
            directoryStream
                .newByteChannel(
                    keyName,
                    setOf(StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS),
                ).use { channel ->
                    val buffer = ByteBuffer.allocate(MAX_KEY_FILE_BYTES + 1)
                    while (buffer.hasRemaining() && channel.read(buffer) != -1) {
                        // 같은 descriptor에서 상한까지만 읽어 교체·증가한 파일을 fail-closed 한다.
                    }
                    require(!buffer.hasRemaining().not() || channel.read(ByteBuffer.allocate(1)) == -1)
                    buffer.flip()
                    ByteArray(buffer.remaining()).also(buffer::get)
                }
        val after = view.readAttributes()
        require(before.fileKey() != null && before.fileKey() == after.fileKey())
        require(before.size() == after.size() && bytes.size.toLong() == before.size())
        require(linkCount(keyPath) == 1L)
        return bytes.toString(Charsets.US_ASCII).trim()
    }

    private fun linkCount(path: Path): Long = (Files.getAttribute(path, "unix:nlink", LinkOption.NOFOLLOW_LINKS) as Number).toLong()

    private fun validateKeyAttributes(
        attributes: PosixFileAttributes,
        expectedOwner: java.nio.file.attribute.UserPrincipal,
        oldKey: Boolean,
    ) {
        require(attributes.isRegularFile && !attributes.isSymbolicLink)
        require(attributes.permissions() == FILE_PERMISSIONS)
        require(attributes.owner() == expectedOwner)
        require(attributes.size() in MIN_KEY_FILE_BYTES..MAX_KEY_FILE_BYTES.toLong())
        if (oldKey) {
            val age = Duration.between(attributes.lastModifiedTime().toInstant(), Instant.now(clock))
            require(!age.isNegative && age <= MAX_OLD_KEY_AGE)
        }
    }

    private fun padBase64(value: String): String = value + "=".repeat((4 - value.length % 4) % 4)

    private companion object {
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
        val HEX_KEY = Regex("^[0-9a-f]{64}$")
        const val MIN_KEY_FILE_BYTES = 43L
        const val MAX_KEY_FILE_BYTES = 66
        val MAX_OLD_KEY_AGE: Duration = Duration.ofDays(30)
    }
}
