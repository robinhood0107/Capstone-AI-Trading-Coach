package com.capstone.decision

import com.capstone.decision.infrastructure.security.DemoAccounts
import com.capstone.decision.infrastructure.security.DemoCredentialBundlePolicy
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.nio.file.StandardOpenOption
import java.nio.file.attribute.PosixFilePermission
import java.nio.file.attribute.PosixFilePermissions
import java.security.SecureRandom
import java.util.Base64

// OpenAPI boot에 필요한 non-provider secret을 매 실행 새로 만들고 값은 stdout이나 tracked 파일에 남기지 않는다.
object OpenApiFixtureEnvironmentWriter {
    @JvmStatic
    fun main(args: Array<String>) {
        require(args.size == 1) { "OpenAPI fixture writer requires one output path." }
        val projectRoot = Path.of(System.getProperty("user.dir")).toAbsolutePath().normalize()
        val buildRoot = projectRoot.resolve("build").normalize()
        val output = Path.of(args.single()).toAbsolutePath().normalize()
        require(output.startsWith(buildRoot)) { "OpenAPI fixture environment must stay under build/." }

        val random = SecureRandom()
        val separationKey = ByteArray(32).also(random::nextBytes)
        val grpcSharedSecret = randomToken(random, 32)
        val userPassword = randomToken(random, 18).toCharArray()
        val adminPassword = randomToken(random, 18).toCharArray()
        val encoder = BCryptPasswordEncoder(12)
        try {
            val values =
                linkedMapOf(
                    "POSTGRES_DB" to "trading",
                    "POSTGRES_ADMIN_USER" to "postgres",
                    "POSTGRES_HOST" to "127.0.0.1",
                    "POSTGRES_HOST_PORT" to "55432",
                    "POSTGRES_PORT" to "55432",
                    "POSTGRES_ADMIN_PASSWORD" to randomToken(random, 32),
                    "POSTGRES_APP_PASSWORD" to randomToken(random, 32),
                    "POSTGRES_MIGRATION_PASSWORD" to randomToken(random, 32),
                    "POSTGRES_COLLECTOR_PASSWORD" to randomToken(random, 32),
                    "POSTGRES_DISCLOSURE_READER_PASSWORD" to randomToken(random, 32),
                    "POSTGRES_MARKET_WRITER_PASSWORD" to randomToken(random, 32),
                    "POSTGRES_PORTFOLIO_WRITER_PASSWORD" to randomToken(random, 32),
                    "POSTGRES_RISK_WRITER_PASSWORD" to randomToken(random, 32),
                    "DECISION_GRPC_SHARED_SECRET" to grpcSharedSecret,
                    "PYTHON_GRPC_SHARED_SECRET" to grpcSharedSecret,
                    "REDIS_PASSWORD" to randomToken(random, 32),
                    "JWT_SECRET" to randomToken(random, 32),
                    "JWT_ISSUER" to "s21-openapi-local",
                    "JWT_AUDIENCE" to "s21-openapi-client",
                    "LOGIN_SCOPE_HMAC_KEY" to randomToken(random, 32),
                    "PRINCIPLE_CURSOR_HMAC_KEY" to randomToken(random, 32),
                    "DECISION_IDEMPOTENCY_SCOPE_HMAC_KEY" to randomToken(random, 32),
                    "BROKERAGE_IDEMPOTENCY_SCOPE_HMAC_KEY" to randomToken(random, 32),
                    "DEMO_CREDENTIAL_SEPARATION_KEY" to encode(separationKey),
                    "DEMO_USER_CREDENTIAL_BUNDLE" to
                        DemoCredentialBundlePolicy.prepare(
                            requireNotNull(DemoAccounts.byUserId("usr_demo_user")),
                            userPassword,
                            separationKey,
                            encoder,
                        ),
                    "DEMO_ADMIN_CREDENTIAL_BUNDLE" to
                        DemoCredentialBundlePolicy.prepare(
                            requireNotNull(DemoAccounts.byUserId("usr_demo_admin")),
                            adminPassword,
                            separationKey,
                            encoder,
                        ),
                )
            writePrivateEnvironment(output, values)
            println("OpenAPI fixture environment created at ${projectRoot.relativize(output)}")
        } finally {
            separationKey.fill(0)
            userPassword.fill('\u0000')
            adminPassword.fill('\u0000')
        }
    }

    private fun randomToken(
        random: SecureRandom,
        byteCount: Int,
    ): String {
        val bytes = ByteArray(byteCount).also(random::nextBytes)
        return try {
            encode(bytes)
        } finally {
            bytes.fill(0)
        }
    }

    private fun encode(value: ByteArray): String = Base64.getUrlEncoder().withoutPadding().encodeToString(value)

    private fun writePrivateEnvironment(
        output: Path,
        values: Map<String, String>,
    ) {
        Files.createDirectories(output.parent)
        val permissions =
            setOf(
                PosixFilePermission.OWNER_READ,
                PosixFilePermission.OWNER_WRITE,
            )
        val temporary =
            Files.createTempFile(
                output.parent,
                ".openapi-env-",
                ".tmp",
                PosixFilePermissions.asFileAttribute(permissions),
            )
        try {
            val text = values.entries.joinToString(separator = "\n", postfix = "\n") { (name, value) -> "$name='$value'" }
            Files.writeString(
                temporary,
                text,
                StandardCharsets.UTF_8,
                StandardOpenOption.TRUNCATE_EXISTING,
            )
            Files.move(
                temporary,
                output,
                StandardCopyOption.ATOMIC_MOVE,
                StandardCopyOption.REPLACE_EXISTING,
            )
            Files.setPosixFilePermissions(output, permissions)
        } finally {
            Files.deleteIfExists(temporary)
        }
    }
}
