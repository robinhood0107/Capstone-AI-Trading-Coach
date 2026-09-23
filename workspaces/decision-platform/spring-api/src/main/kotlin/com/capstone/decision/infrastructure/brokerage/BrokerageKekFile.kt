package com.capstone.decision.infrastructure.brokerage

import org.springframework.beans.factory.annotation.Value
import org.springframework.context.annotation.Profile
import org.springframework.stereotype.Component
import java.nio.ByteBuffer
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.nio.file.attribute.PosixFileAttributes
import java.nio.file.attribute.PosixFilePermission

/**
 * Brokerage credentials have their own 32-byte KEK, separate from RAG, JWT and order-reference keys.
 * The operator writes one 0600 raw key under a 0700 directory; the file never enters an image or DB.
 */
@Component
@Profile("mars-full")
class BrokerageKekFile(
    @Value("\${mars.brokerage.kek-directory:}") private val directoryName: String,
) {
    init {
        load().fill(0)
    }

    fun load(version: String = CURRENT_VERSION): ByteArray {
        try {
            require(version == CURRENT_VERSION)
            val directory = Path.of(directoryName)
            require(directory.isAbsolute && directory.normalize() == directory)
            val directoryAttributes = Files.readAttributes(directory, PosixFileAttributes::class.java, LinkOption.NOFOLLOW_LINKS)
            require(directoryAttributes.isDirectory && !directoryAttributes.isSymbolicLink)
            require(directoryAttributes.permissions() == DIRECTORY_PERMISSIONS)
            val processOwner =
                directory.fileSystem.userPrincipalLookupService.lookupPrincipalByName(System.getProperty("user.name"))
            require(directoryAttributes.owner() == processOwner)
            val path = directory.resolve("brokerage-$version.key")
            val before = Files.readAttributes(path, PosixFileAttributes::class.java, LinkOption.NOFOLLOW_LINKS)
            require(before.isRegularFile && !before.isSymbolicLink && before.size() == KEY_BYTES.toLong())
            require(before.permissions() == FILE_PERMISSIONS && before.owner() == directoryAttributes.owner())
            require((Files.getAttribute(path, "unix:nlink", LinkOption.NOFOLLOW_LINKS) as Number).toLong() == 1L)
            val key =
                Files.newByteChannel(path, setOf(StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS)).use { channel ->
                    val buffer = ByteBuffer.allocate(KEY_BYTES + 1)
                    while (buffer.hasRemaining() && channel.read(buffer) != -1) {
                        // Read from one descriptor; extra bytes fail closed.
                    }
                    require(buffer.position() == KEY_BYTES)
                    buffer.flip()
                    ByteArray(KEY_BYTES).also(buffer::get)
                }
            val after = Files.readAttributes(path, PosixFileAttributes::class.java, LinkOption.NOFOLLOW_LINKS)
            require(before.fileKey() != null && before.fileKey() == after.fileKey() && before.size() == after.size())
            require((Files.getAttribute(path, "unix:nlink", LinkOption.NOFOLLOW_LINKS) as Number).toLong() == 1L)
            return key
        } catch (_: Exception) {
            throw IllegalStateException("BROKERAGE_KEK_UNAVAILABLE")
        }
    }

    companion object {
        const val CURRENT_VERSION = "kek-v1"
        const val KEY_BYTES = 32
        private val DIRECTORY_PERMISSIONS =
            setOf(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE, PosixFilePermission.OWNER_EXECUTE)
        private val FILE_PERMISSIONS = setOf(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE)
    }
}
