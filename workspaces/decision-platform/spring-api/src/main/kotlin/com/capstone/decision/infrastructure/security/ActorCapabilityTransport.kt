package com.capstone.decision.infrastructure.security

import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import com.zaxxer.hikari.HikariConfig
import com.zaxxer.hikari.HikariDataSource
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Duration
import java.util.concurrent.Executors

object ActorCapabilityWire {
    const val CONTENT_TYPE = "application/vnd.capstone.actor-capability.v1+text"
    const val ISSUE_PATH = "/internal/actor-capabilities/issue"
    const val MAX_BODY_BYTES = 2_048

    fun encode(
        identityHandle: String,
        binding: ActorCapabilityBinding,
    ): ByteArray =
        listOf(
            "p1-actor-capability-request.v1",
            identityHandle,
            binding.operation,
            binding.targetKind,
            binding.targetId,
            binding.payloadHash,
            binding.rolePolicy.name,
        ).joinToString("\n").toByteArray(StandardCharsets.UTF_8).also { require(it.size <= MAX_BODY_BYTES) }

    fun decode(bytes: ByteArray): Pair<String, ActorCapabilityBinding> {
        require(bytes.size in 1..MAX_BODY_BYTES)
        val values = bytes.toString(StandardCharsets.UTF_8).split('\n')
        require(values.size == 7 && values[0] == "p1-actor-capability-request.v1")
        val identityHandle = values[1]
        require(identityHandle.matches(Regex("^idh1_[0-9a-f]{64}$")))
        return identityHandle to
            ActorCapabilityBinding(
                operation = values[2],
                targetKind = values[3],
                targetId = values[4],
                payloadHash = values[5],
                rolePolicy = ActorCapabilityRolePolicy.valueOf(values[6]),
            )
    }
}

@ConfigurationProperties("app.actor-capability")
data class ActorCapabilityClientProperties(
    val authorityUrl: String = "",
    val sharedSecret: String = "",
    val publicKey: String = "",
) {
    fun validatedUri(): URI {
        require(sharedSecret.length in 32..256 && sharedSecret.none { it == '\r' || it == '\n' })
        ActorCapabilityKeyCodec.publicKey(publicKey)
        val uri = URI.create(authorityUrl)
        require(
            uri.scheme == "http" &&
                uri.host == "127.0.0.1" &&
                uri.port in 1..65535 &&
                uri.path == ActorCapabilityWire.ISSUE_PATH &&
                uri.rawQuery == null &&
                uri.rawFragment == null &&
                uri.userInfo == null,
        )
        return uri
    }
}

@Configuration
@EnableConfigurationProperties(ActorCapabilityClientProperties::class)
@ConditionalOnProperty(prefix = "app.actor-capability", name = ["transport"], havingValue = "http")
class ActorCapabilityClientConfiguration {
    @Bean
    fun httpActorCapabilityIssuer(
        properties: ActorCapabilityClientProperties,
        identityHandleIssuer: ActorIdentityHandleIssuer,
    ): ActorCapabilityIssuer = HttpActorCapabilityIssuer(properties, identityHandleIssuer)
}

class HttpActorCapabilityIssuer(
    properties: ActorCapabilityClientProperties,
    private val identityHandleIssuer: ActorIdentityHandleIssuer,
    private val client: HttpClient =
        HttpClient
            .newBuilder()
            .connectTimeout(Duration.ofSeconds(1))
            .followRedirects(HttpClient.Redirect.NEVER)
            .build(),
) : ActorCapabilityIssuer {
    private val uri = properties.validatedUri()
    private val sharedSecret = properties.sharedSecret
    private val publicKey = ActorCapabilityKeyCodec.publicKey(properties.publicKey)

    override fun issue(
        actorUserId: String,
        binding: ActorCapabilityBinding,
    ): String {
        val identityHandle = identityHandleIssuer.issue(actorUserId, binding)
        val request =
            HttpRequest
                .newBuilder(uri)
                .timeout(Duration.ofSeconds(2))
                .header("Authorization", "Bearer $sharedSecret")
                .header("Content-Type", ActorCapabilityWire.CONTENT_TYPE)
                .POST(HttpRequest.BodyPublishers.ofByteArray(ActorCapabilityWire.encode(identityHandle, binding)))
                .build()
        val response = client.send(request, HttpResponse.BodyHandlers.ofByteArray())
        if (response.statusCode() != 200 || response.body().size !in 120..1_024) {
            throw ActorCapabilityDeniedException()
        }
        val token = response.body().toString(StandardCharsets.US_ASCII)
        ActorCapabilityPacketCodec.verifyBound(token, publicKey, actorUserId, binding)
        return token
    }
}

object ActorCapabilityAuthorityMain {
    @JvmStatic
    fun main(args: Array<String>) {
        require(args.isEmpty())
        val port = requiredEnv("ACTOR_CAPABILITY_AUTHORITY_PORT").toInt().also { require(it in 1..65535) }
        val sharedSecret = requiredEnv("ACTOR_CAPABILITY_SHARED_SECRET")
        require(sharedSecret.length in 32..256 && sharedSecret.none { it == '\r' || it == '\n' })
        val privateKey = ActorCapabilityKeyCodec.privateKey(requiredEnv("ACTOR_CAPABILITY_PRIVATE_KEY"))
        val publicKey = ActorCapabilityKeyCodec.publicKey(requiredEnv("ACTOR_CAPABILITY_PUBLIC_KEY"))
        val dataSource =
            HikariDataSource(
                HikariConfig().apply {
                    jdbcUrl = requiredEnv("ACTOR_IDENTITY_JDBC_URL")
                    require(jdbcUrl.startsWith("jdbc:postgresql://") && !jdbcUrl.contains(Regex("[\\r\\n]")))
                    username = "decision_identity"
                    password = requiredEnv("POSTGRES_IDENTITY_PASSWORD")
                    maximumPoolSize = 2
                    minimumIdle = 0
                    connectionTimeout = 1_000
                    validationTimeout = 500
                    initializationFailTimeout = 1_000
                    poolName = "actor-capability-authority-pool"
                },
            )
        val authority = DatabaseActorCapabilityAuthority(dataSource, privateKey, publicKey)
        val server = HttpServer.create(InetSocketAddress(InetAddress.getByName("127.0.0.1"), port), 16)
        val executor = Executors.newFixedThreadPool(4)
        server.executor = executor
        server.createContext(ActorCapabilityWire.ISSUE_PATH) { exchange ->
            handle(exchange, sharedSecret, authority)
        }
        Runtime.getRuntime().addShutdownHook(
            Thread {
                server.stop(0)
                executor.shutdownNow()
                dataSource.close()
            },
        )
        server.start()
    }

    private fun handle(
        exchange: HttpExchange,
        sharedSecret: String,
        authority: DatabaseActorCapabilityAuthority,
    ) {
        try {
            val authorized =
                exchange.requestHeaders.getFirst("Authorization")?.let { supplied ->
                    MessageDigest.isEqual(
                        supplied.toByteArray(StandardCharsets.UTF_8),
                        "Bearer $sharedSecret".toByteArray(StandardCharsets.UTF_8),
                    )
                } == true
            if (!authorized) return exchange.respond(403)
            if (exchange.requestMethod != "POST" || exchange.requestHeaders.getFirst("Content-Type") != ActorCapabilityWire.CONTENT_TYPE) {
                return exchange.respond(400)
            }
            val declaredLength = exchange.requestHeaders.getFirst("Content-Length")?.toIntOrNull()
            if (declaredLength == null || declaredLength !in 1..ActorCapabilityWire.MAX_BODY_BYTES) {
                return exchange.respond(400)
            }
            val body = exchange.requestBody.use { it.readNBytes(ActorCapabilityWire.MAX_BODY_BYTES + 1) }
            if (body.size != declaredLength || body.size > ActorCapabilityWire.MAX_BODY_BYTES) return exchange.respond(400)
            val (identityHandle, binding) = ActorCapabilityWire.decode(body)
            exchange.respond(200, authority.issue(identityHandle, binding).toByteArray(StandardCharsets.US_ASCII))
        } catch (_: IllegalArgumentException) {
            exchange.respond(400)
        } catch (_: ActorCapabilityDeniedException) {
            exchange.respond(403)
        } catch (_: RuntimeException) {
            exchange.respond(503)
        } finally {
            exchange.close()
        }
    }

    private fun HttpExchange.respond(
        status: Int,
        body: ByteArray = ByteArray(0),
    ) {
        responseHeaders.set("Content-Type", "text/plain; charset=us-ascii")
        responseHeaders.set("Cache-Control", "no-store")
        sendResponseHeaders(status, body.size.toLong())
        if (body.isNotEmpty()) responseBody.use { it.write(body) }
    }

    private fun requiredEnv(name: String): String =
        requireNotNull(System.getenv(name)?.takeIf { it.isNotBlank() }) {
            "$name is required."
        }
}
