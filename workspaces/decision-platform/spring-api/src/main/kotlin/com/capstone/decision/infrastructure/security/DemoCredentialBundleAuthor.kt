package com.capstone.decision.infrastructure.security

import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder
import java.nio.ByteBuffer
import java.nio.channels.FileChannel
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.nio.file.attribute.PosixFilePermissions
import kotlin.system.exitProcess

object DemoCredentialBundleAuthor {
    @JvmStatic
    fun main(args: Array<String>) {
        if (args.isNotEmpty()) {
            System.err.println("demo credential author failed: unexpected_arguments")
            exitProcess(1)
        }
        try {
            author(System.getenv())
            println("demo credential bundle authored")
        } catch (error: Exception) {
            // 모든 예외를 한 문자열로 묶으면 신규 clone에서 어느 검증에 걸렸는지 특정할 수 없다.
            // 이 경로의 예외 메시지는 값이 아니라 조건만 서술하므로 그대로 노출해도 비밀이 새지
            // 않는다. 다만 개행을 지우고 길이를 제한해 로그 오염과 예기치 않은 내용을 막는다.
            val reason =
                (error.message ?: error::class.java.simpleName)
                    .replace(Regex("[\\r\\n]+"), " ")
                    .take(200)
            System.err.println("demo credential author failed: $reason")
            exitProcess(1)
        }
    }

    fun author(environment: Map<String, String>) {
        val userId = required(environment, "DEMO_CREDENTIAL_USER_ID")
        val identity = DemoAccounts.byUserId(userId) ?: error("identity is not allowlisted")
        val passwordPath = secureInput(environment, "DEMO_CREDENTIAL_PASSWORD_FILE", 512)
        val keyPath = secureInput(environment, "DEMO_CREDENTIAL_SEPARATION_KEY_FILE", 256)
        val output = Path.of(required(environment, "DEMO_CREDENTIAL_BUNDLE_OUTPUT")).toAbsolutePath().normalize()
        require(!Files.exists(output, LinkOption.NOFOLLOW_LINKS)) {
            "bundle output already exists"
        }
        val parent = requireNotNull(output.parent) { "bundle output has no parent directory" }
        require(Files.isDirectory(parent, LinkOption.NOFOLLOW_LINKS) && !Files.isSymbolicLink(parent)) {
            "bundle output parent is not a real directory"
        }
        val password = Files.readString(passwordPath, StandardCharsets.UTF_8).trimEnd('\n', '\r').toCharArray()
        val key =
            DemoCredentialBundlePolicy.decodeSeparationKey(
                Files.readString(keyPath, StandardCharsets.UTF_8).trimEnd('\n', '\r'),
            )
        try {
            val bundle = DemoCredentialBundlePolicy.prepare(identity, password, key, BCryptPasswordEncoder(12))
            val content = bundle.toByteArray(StandardCharsets.UTF_8)
            try {
                FileChannel
                    .open(
                        output,
                        setOf(StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE),
                        PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")),
                    ).use { channel ->
                        val buffer = ByteBuffer.wrap(content)
                        while (buffer.hasRemaining()) channel.write(buffer)
                        channel.force(true)
                    }
            } finally {
                content.fill(0)
            }
        } finally {
            password.fill('\u0000')
            key.fill(0)
        }
    }

    private fun secureInput(
        environment: Map<String, String>,
        name: String,
        maxBytes: Long,
    ): Path {
        val path = Path.of(required(environment, name)).toAbsolutePath().normalize()
        require(Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS) && !Files.isSymbolicLink(path)) {
            "$name is not a regular file"
        }
        require(Files.size(path) in 1..maxBytes) { "$name size is outside 1..$maxBytes bytes" }
        return path
    }

    private fun required(
        environment: Map<String, String>,
        name: String,
    ): String = environment[name]?.takeIf(String::isNotBlank) ?: error("$name is required")
}
