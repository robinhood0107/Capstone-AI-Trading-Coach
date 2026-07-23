package com.capstone.decision.infrastructure.principle

import com.capstone.decision.application.principle.HistoryCursor
import com.capstone.decision.application.principle.HistorySort
import com.capstone.decision.application.principle.InvalidPrincipleCursorException
import com.capstone.decision.application.principle.OwnerCursor
import com.capstone.decision.application.principle.OwnerSort
import com.capstone.decision.application.principle.PrincipleContract
import com.capstone.decision.application.principle.PrincipleCursorPort
import com.capstone.decision.domain.principle.PrincipleId
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.stereotype.Component
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Clock
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.Base64
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

@ConfigurationProperties("app.principle")
data class PrincipleProperties(
    var cursorHmacKey: String = "",
) {
    fun validate() {
        require(cursorHmacKey.toByteArray(StandardCharsets.UTF_8).size >= 32) {
            "app.principle.cursor-hmac-key must be at least 32 bytes."
        }
    }
}

@Configuration(proxyBeanMethods = false)
class PrincipleConfiguration {
    @Bean
    fun principleClock(): Clock = Clock.systemUTC()
}

// 서명 검증 뒤에도 canonical JSON과 route/subject/resource/sort/size/time binding을 모두 다시 검증한다.
@Component
class PrincipleCursorCodec(
    private val properties: PrincipleProperties,
    private val catalog: PrincipleContract,
    private val principleClock: Clock,
    private val objectMapper: ObjectMapper,
) : PrincipleCursorPort {
    init {
        properties.validate()
    }

    override fun encodeOwner(
        userId: String,
        size: Int,
        sort: String,
        updatedAt: OffsetDateTime,
        principleId: String,
    ): String {
        require(size in catalog.pageMin..catalog.pageMax)
        require(sort in OwnerSort.entries.map(Enum<*>::name))
        require(PrincipleId.isValid(principleId))
        val issuedAt = principleClock.instant().epochSecond
        return encode(
            ownerCanonical(
                OwnerPayload(
                    subjectBinding = subjectBinding(userId),
                    sort = sort,
                    size = size,
                    issuedAt = issuedAt,
                    expiresAt = Math.addExact(issuedAt, catalog.cursorTtlSeconds),
                    lastUpdatedAt = updatedAt.toInstant().toString(),
                    lastPrincipleId = principleId,
                ),
            ),
        )
    }

    override fun decodeOwner(
        cursor: String,
        userId: String,
        requestedSize: Int?,
        requestedSort: String?,
    ): OwnerCursor {
        val payload = decode(cursor)
        requireBinding(payload, OWNER_ROUTE, userId, requestedSize, requestedSort)
        val owner = payload as? OwnerPayload ?: throw InvalidPrincipleCursorException()
        return OwnerCursor(
            size = owner.size,
            sort = owner.sort,
            updatedAt = OffsetDateTime.ofInstant(Instant.parse(owner.lastUpdatedAt), ZoneOffset.UTC),
            principleId = owner.lastPrincipleId,
        )
    }

    override fun encodeHistory(
        userId: String,
        principleId: String,
        size: Int,
        sort: String,
        version: Int,
    ): String {
        require(size in catalog.pageMin..catalog.pageMax)
        require(sort in HistorySort.entries.map(Enum<*>::name))
        require(PrincipleId.isValid(principleId))
        require(version in 1..catalog.maxVersion)
        val issuedAt = principleClock.instant().epochSecond
        return encode(
            historyCanonical(
                HistoryPayload(
                    subjectBinding = subjectBinding(userId),
                    sort = sort,
                    size = size,
                    issuedAt = issuedAt,
                    expiresAt = Math.addExact(issuedAt, catalog.cursorTtlSeconds),
                    principleId = principleId,
                    lastVersion = version,
                ),
            ),
        )
    }

    override fun decodeHistory(
        cursor: String,
        userId: String,
        principleId: String,
        requestedSize: Int?,
        requestedSort: String?,
    ): HistoryCursor {
        val payload = decode(cursor)
        requireBinding(payload, HISTORY_ROUTE, userId, requestedSize, requestedSort)
        val history = payload as? HistoryPayload ?: throw InvalidPrincipleCursorException()
        if (history.principleId != principleId) {
            throw InvalidPrincipleCursorException()
        }
        return HistoryCursor(
            size = history.size,
            sort = history.sort,
            version = history.lastVersion,
        )
    }

    private fun encode(canonicalPayload: String): String {
        val payloadPart = ENCODER.encodeToString(canonicalPayload.toByteArray(StandardCharsets.UTF_8))
        val signature = ENCODER.encodeToString(mac(SIGNATURE_PURPOSE, payloadPart))
        return "$payloadPart.$signature".also {
            check(it.length <= catalog.cursorMaxChars) { "Generated Principle cursor exceeded its contract limit." }
        }
    }

    private fun decode(cursor: String): CursorPayload {
        try {
            require(cursor.length in 1..catalog.cursorMaxChars)
            require('=' !in cursor)
            val parts = cursor.split('.')
            require(parts.size == 2 && parts.all(String::isNotEmpty))
            val expected = mac(SIGNATURE_PURPOSE, parts[0])
            val actual = DECODER.decode(parts[1])
            require(actual.size == HMAC_BYTES)
            require(MessageDigest.isEqual(expected, actual))

            val payloadBytes = DECODER.decode(parts[0])
            require(payloadBytes.isNotEmpty())
            val payloadText = String(payloadBytes, StandardCharsets.UTF_8)
            require(payloadText.toByteArray(StandardCharsets.UTF_8).contentEquals(payloadBytes))
            val root = objectMapper.readTree(payloadBytes)
            require(root != null && root.isObject)
            requireCommon(root)

            val payload =
                when (root.path("route").stringValue()) {
                    OWNER_ROUTE -> decodeOwnerPayload(root)
                    HISTORY_ROUTE -> decodeHistoryPayload(root)
                    else -> throw IllegalArgumentException("Unsupported Principle cursor route.")
                }
            val canonical =
                when (payload) {
                    is OwnerPayload -> ownerCanonical(payload)
                    is HistoryPayload -> historyCanonical(payload)
                }
            require(payloadText == canonical)
            return payload
        } catch (_: RuntimeException) {
            throw InvalidPrincipleCursorException()
        }
    }

    private fun requireCommon(root: JsonNode) {
        require(root.path("schemaVersion").isIntegralNumber)
        require(root.path("schemaVersion").intValue() == SCHEMA_VERSION)
        require(root.path("keyVersion").isString)
        require(root.path("keyVersion").stringValue() == KEY_VERSION)
        require(root.path("route").isString)
        require(root.path("subjectBinding").isString)
        validateSubjectBinding(root.path("subjectBinding").stringValue())
        require(root.path("filters").isObject && root.path("filters").size() == 0)
        require(root.path("sort").isString)
        require(root.path("size").isIntegralNumber)
        require(root.path("issuedAt").isIntegralNumber)
        require(root.path("expiresAt").isIntegralNumber)

        val size = root.path("size").intValue()
        val issuedAt = root.path("issuedAt").longValue()
        val expiresAt = root.path("expiresAt").longValue()
        val now = principleClock.instant().epochSecond
        require(size in catalog.pageMin..catalog.pageMax)
        require(issuedAt <= Math.addExact(now, FUTURE_SKEW_SECONDS))
        require(expiresAt > now)
        require(expiresAt == Math.addExact(issuedAt, catalog.cursorTtlSeconds))
    }

    private fun decodeOwnerPayload(root: JsonNode): OwnerPayload {
        require(root.propertyNames().asSequence().toList() == OWNER_FIELDS)
        val sort = root.path("sort").stringValue()
        val lastUpdatedAt = root.path("lastUpdatedAt").stringValue()
        val lastPrincipleId = root.path("lastPrincipleId").stringValue()
        require(sort in OwnerSort.entries.map(Enum<*>::name))
        require(PrincipleId.isValid(lastPrincipleId))
        require(Instant.parse(lastUpdatedAt).toString() == lastUpdatedAt)
        return OwnerPayload(
            subjectBinding = root.path("subjectBinding").stringValue(),
            sort = sort,
            size = root.path("size").intValue(),
            issuedAt = root.path("issuedAt").longValue(),
            expiresAt = root.path("expiresAt").longValue(),
            lastUpdatedAt = lastUpdatedAt,
            lastPrincipleId = lastPrincipleId,
        )
    }

    private fun decodeHistoryPayload(root: JsonNode): HistoryPayload {
        require(root.propertyNames().asSequence().toList() == HISTORY_FIELDS)
        val sort = root.path("sort").stringValue()
        val principleId = root.path("principleId").stringValue()
        val lastVersionNode = root.path("lastVersion")
        require(sort in HistorySort.entries.map(Enum<*>::name))
        require(PrincipleId.isValid(principleId))
        require(lastVersionNode.isIntegralNumber)
        val lastVersion = lastVersionNode.intValue()
        require(lastVersion in 1..catalog.maxVersion)
        return HistoryPayload(
            subjectBinding = root.path("subjectBinding").stringValue(),
            sort = sort,
            size = root.path("size").intValue(),
            issuedAt = root.path("issuedAt").longValue(),
            expiresAt = root.path("expiresAt").longValue(),
            principleId = principleId,
            lastVersion = lastVersion,
        )
    }

    private fun requireBinding(
        payload: CursorPayload,
        route: String,
        userId: String,
        requestedSize: Int?,
        requestedSort: String?,
    ) {
        try {
            require(payload.route == route)
            require(
                MessageDigest.isEqual(
                    payload.subjectBinding.toByteArray(StandardCharsets.US_ASCII),
                    subjectBinding(userId).toByteArray(StandardCharsets.US_ASCII),
                ),
            )
            require(requestedSize == null || requestedSize == payload.size)
            require(requestedSort == null || requestedSort == payload.sort)
        } catch (_: IllegalArgumentException) {
            throw InvalidPrincipleCursorException()
        }
    }

    private fun validateSubjectBinding(value: String) {
        require('=' !in value)
        val decoded = DECODER.decode(value)
        require(decoded.size == HMAC_BYTES)
        require(ENCODER.encodeToString(decoded) == value)
    }

    private fun ownerCanonical(payload: OwnerPayload): String =
        """{"schemaVersion":$SCHEMA_VERSION,"keyVersion":"$KEY_VERSION","route":"$OWNER_ROUTE","subjectBinding":"${payload.subjectBinding}","filters":{},"sort":"${payload.sort}","size":${payload.size},"issuedAt":${payload.issuedAt},"expiresAt":${payload.expiresAt},"lastUpdatedAt":"${payload.lastUpdatedAt}","lastPrincipleId":"${payload.lastPrincipleId}"}"""

    private fun historyCanonical(payload: HistoryPayload): String =
        """{"schemaVersion":$SCHEMA_VERSION,"keyVersion":"$KEY_VERSION","route":"$HISTORY_ROUTE","subjectBinding":"${payload.subjectBinding}","filters":{},"sort":"${payload.sort}","size":${payload.size},"issuedAt":${payload.issuedAt},"expiresAt":${payload.expiresAt},"principleId":"${payload.principleId}","lastVersion":${payload.lastVersion}}"""

    private fun subjectBinding(userId: String): String = ENCODER.encodeToString(mac(SUBJECT_PURPOSE, userId))

    private fun mac(
        purpose: String,
        value: String,
    ): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(properties.cursorHmacKey.toByteArray(StandardCharsets.UTF_8), "HmacSHA256"))
        mac.update(
            "capstone-principle-cursor\u0000$SCHEMA_VERSION\u0000$KEY_VERSION\u0000$purpose\u0000"
                .toByteArray(StandardCharsets.UTF_8),
        )
        return mac.doFinal(value.toByteArray(StandardCharsets.UTF_8))
    }

    private sealed interface CursorPayload {
        val route: String
        val subjectBinding: String
        val sort: String
        val size: Int
        val issuedAt: Long
        val expiresAt: Long
    }

    private data class OwnerPayload(
        override val subjectBinding: String,
        override val sort: String,
        override val size: Int,
        override val issuedAt: Long,
        override val expiresAt: Long,
        val lastUpdatedAt: String,
        val lastPrincipleId: String,
    ) : CursorPayload {
        override val route: String = OWNER_ROUTE
    }

    private data class HistoryPayload(
        override val subjectBinding: String,
        override val sort: String,
        override val size: Int,
        override val issuedAt: Long,
        override val expiresAt: Long,
        val principleId: String,
        val lastVersion: Int,
    ) : CursorPayload {
        override val route: String = HISTORY_ROUTE
    }

    companion object {
        private const val SCHEMA_VERSION = 1
        private const val KEY_VERSION = "v1"
        private const val OWNER_ROUTE = "principles"
        private const val HISTORY_ROUTE = "principle-versions"
        private const val SUBJECT_PURPOSE = "subject"
        private const val SIGNATURE_PURPOSE = "signature"
        private const val FUTURE_SKEW_SECONDS = 60L
        private const val HMAC_BYTES = 32
        private val OWNER_FIELDS =
            listOf(
                "schemaVersion",
                "keyVersion",
                "route",
                "subjectBinding",
                "filters",
                "sort",
                "size",
                "issuedAt",
                "expiresAt",
                "lastUpdatedAt",
                "lastPrincipleId",
            )
        private val HISTORY_FIELDS =
            listOf(
                "schemaVersion",
                "keyVersion",
                "route",
                "subjectBinding",
                "filters",
                "sort",
                "size",
                "issuedAt",
                "expiresAt",
                "principleId",
                "lastVersion",
            )
        private val ENCODER = Base64.getUrlEncoder().withoutPadding()
        private val DECODER = Base64.getUrlDecoder()
    }
}
