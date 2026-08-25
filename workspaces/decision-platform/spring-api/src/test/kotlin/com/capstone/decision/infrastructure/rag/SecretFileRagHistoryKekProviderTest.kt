package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagHistoryCorruptedException
import org.junit.jupiter.api.Assertions.assertArrayEquals
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.FileTime
import java.nio.file.attribute.PosixFilePermission
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset

class SecretFileRagHistoryKekProviderTest {
    @TempDir
    lateinit var temporaryDirectory: Path

    @Test
    fun `provider accepts only exact owner-only directory and regular single-link key file`() {
        val secretDirectory = secureDirectory("valid")
        val key = ByteArray(32) { index -> (index + 1).toByte() }
        writeKey(secretDirectory, "kek-v2", key)
        val provider = provider(secretDirectory, "kek-v2")

        val loaded = provider.current()

        assertEquals("kek-v2", loaded.version)
        assertArrayEquals(key, loaded.keyBytes)
    }

    @Test
    fun `provider rejects directory and file permission widening`() {
        val broadDirectory = secureDirectory("broad-directory")
        writeKey(broadDirectory, "kek-v1", ByteArray(32) { 1 })
        Files.setPosixFilePermissions(
            broadDirectory,
            setOf(
                PosixFilePermission.OWNER_READ,
                PosixFilePermission.OWNER_WRITE,
                PosixFilePermission.OWNER_EXECUTE,
                PosixFilePermission.GROUP_READ,
            ),
        )
        assertThrows(RagHistoryCorruptedException::class.java) {
            provider(broadDirectory, "kek-v1").current()
        }

        val broadFileDirectory = secureDirectory("broad-file")
        val keyPath = writeKey(broadFileDirectory, "kek-v1", ByteArray(32) { 2 })
        Files.setPosixFilePermissions(
            keyPath,
            setOf(
                PosixFilePermission.OWNER_READ,
                PosixFilePermission.OWNER_WRITE,
                PosixFilePermission.GROUP_READ,
            ),
        )
        assertThrows(RagHistoryCorruptedException::class.java) {
            provider(broadFileDirectory, "kek-v1").current()
        }
    }

    @Test
    fun `provider rejects symlink hardlink malformed and expired old key`() {
        val symlinkDirectory = secureDirectory("symlink")
        val outside = writeKey(secureDirectory("outside"), "kek-v1", ByteArray(32) { 3 })
        Files.createSymbolicLink(symlinkDirectory.resolve("rag-history-kek-v1.key"), outside)
        assertThrows(RagHistoryCorruptedException::class.java) {
            provider(symlinkDirectory, "kek-v1").current()
        }

        val hardlinkDirectory = secureDirectory("hardlink")
        val hardlinkSource = writeKey(hardlinkDirectory, "kek-v1", ByteArray(32) { 4 })
        Files.createLink(hardlinkDirectory.resolve("copy.key"), hardlinkSource)
        assertThrows(RagHistoryCorruptedException::class.java) {
            provider(hardlinkDirectory, "kek-v1").current()
        }

        val malformedDirectory = secureDirectory("malformed")
        val malformed = malformedDirectory.resolve("rag-history-kek-v1.key")
        Files.writeString(malformed, "not-a-key")
        Files.setPosixFilePermissions(malformed, FILE_PERMISSIONS)
        assertThrows(RagHistoryCorruptedException::class.java) {
            provider(malformedDirectory, "kek-v1").current()
        }

        val oldDirectory = secureDirectory("old")
        val old = writeKey(oldDirectory, "kek-v1", ByteArray(32) { 5 })
        writeKey(oldDirectory, "kek-v2", ByteArray(32) { 6 })
        Files.setLastModifiedTime(
            old,
            FileTime.from(Instant.parse("2026-06-29T23:59:59Z")),
        )
        assertThrows(RagHistoryCorruptedException::class.java) {
            provider(oldDirectory, "kek-v2").byVersion("kek-v1")
        }
    }

    private fun provider(
        directory: Path,
        currentVersion: String,
    ): SecretFileRagHistoryKekProvider =
        SecretFileRagHistoryKekProvider(
            properties =
                RagGuardHistoryProperties(
                    historySecretDirectory = directory.toString(),
                    currentKekVersion = currentVersion,
                ),
            clock = Clock.fixed(Instant.parse("2026-07-31T00:00:00Z"), ZoneOffset.UTC),
        )

    private fun secureDirectory(name: String): Path =
        Files.createDirectory(temporaryDirectory.resolve(name)).also { directory ->
            Files.setPosixFilePermissions(directory, DIRECTORY_PERMISSIONS)
        }

    private fun writeKey(
        directory: Path,
        version: String,
        key: ByteArray,
    ): Path {
        val path = directory.resolve("rag-history-$version.key")
        Files.writeString(path, key.joinToString("") { "%02x".format(java.util.Locale.ROOT, it.toInt() and 0xff) })
        Files.setPosixFilePermissions(path, FILE_PERMISSIONS)
        return path
    }

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
    }
}
