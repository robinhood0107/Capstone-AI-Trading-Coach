package com.capstone.decision.infrastructure.brokerage

import org.junit.jupiter.api.Assertions.assertArrayEquals
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermission
import java.security.SecureRandom

class BrokerageCredentialCryptoTest {
    @TempDir
    lateinit var root: Path

    @Test
    fun `sealed mock values cannot be opened for another owner or account`() {
        val crypto = BrokerageCredentialCrypto(BrokerageKekFile(prepareKeyDirectory().toString()))
        val owner = "usr_test_owner"
        val accountId = "acct_" + "a".repeat(32)
        val appKey = "K" + "A".repeat(19)
        val appSecret = "S" + "B".repeat(39)
        val accountNo = "5" + "0".repeat(9)
        crypto.seal(owner, accountId, appKey, appSecret, accountNo).use { sealed ->
            assertFalse(sealed.secretCiphertext.contentEquals(appKey.toByteArray()))
            assertEquals("AAAA", sealed.appKeyLast4)
            assertEquals("0000", sealed.accountNoLast4)
            crypto.open(owner, accountId, sealed).use { opened ->
                assertArrayEquals(appKey.toByteArray(), opened.appKey)
                assertArrayEquals(appSecret.toByteArray(), opened.appSecret)
                assertArrayEquals(accountNo.toByteArray(), opened.accountNo)
            }
            assertThrows(IllegalStateException::class.java) {
                crypto.open("usr_other_owner", accountId, sealed)
            }
            assertThrows(IllegalStateException::class.java) {
                crypto.open(owner, "acct_" + "b".repeat(32), sealed)
            }
            val tamperedTag = sealed.secretTag.clone().also { it[0] = (it[0].toInt() xor 1).toByte() }
            assertThrows(IllegalStateException::class.java) {
                crypto.open(owner, accountId, sealed.copy(secretTag = tamperedTag))
            }
        }
    }

    @Test
    fun `brokerage KEK rejects an unsafe key file`() {
        val directory = prepareKeyDirectory()
        val file = directory.resolve("brokerage-kek-v1.key")
        Files.setPosixFilePermissions(file, setOf(PosixFilePermission.OWNER_READ))
        assertThrows(IllegalStateException::class.java) { BrokerageKekFile(directory.toString()).load() }
    }

    private fun prepareKeyDirectory(): Path {
        val directory = Files.createDirectory(root.resolve("brokerage-" + SecureRandom().nextInt(1_000_000)))
        Files.setPosixFilePermissions(
            directory,
            setOf(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE, PosixFilePermission.OWNER_EXECUTE),
        )
        val key = ByteArray(32).also(SecureRandom()::nextBytes)
        val path = directory.resolve("brokerage-kek-v1.key")
        Files.write(path, key)
        Files.setPosixFilePermissions(path, setOf(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE))
        key.fill(0)
        return directory
    }
}
