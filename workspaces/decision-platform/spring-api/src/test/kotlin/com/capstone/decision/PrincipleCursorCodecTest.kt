package com.capstone.decision

import com.capstone.decision.infrastructure.principle.InvalidPrincipleCursorException
import com.capstone.decision.infrastructure.principle.PrincipleCatalog
import com.capstone.decision.infrastructure.principle.PrincipleCursorCodec
import com.capstone.decision.infrastructure.principle.PrincipleProperties
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import tools.jackson.databind.json.JsonMapper
import java.nio.charset.StandardCharsets
import java.time.Clock
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.Base64

class PrincipleCursorCodecTest {
    private val objectMapper = JsonMapper.builder().build()
    private val catalog = PrincipleCatalog(objectMapper)
    private val properties = PrincipleProperties(cursorHmacKey = "principle-cursor-test-key-32-bytes-minimum")

    @Test
    fun `owner cursor is fixed-order compact JSON with exact binding and lifetime fields`() {
        val issuedAt = Instant.parse("2030-01-02T03:04:05Z")
        val token =
            codec(issuedAt).encodeOwner(
                userId = "usr_sensitive_internal_identifier",
                size = 50,
                sort = "UPDATED_AT_DESC",
                updatedAt = OffsetDateTime.ofInstant(issuedAt.minusSeconds(1), ZoneOffset.UTC),
                principleId = "prc_0123456789abcdef0123456789abcdef",
            )
        val parts = token.split('.')
        val payloadText =
            String(
                Base64.getUrlDecoder().decode(parts.first()),
                StandardCharsets.UTF_8,
            )
        val payload = objectMapper.readTree(payloadText)

        assertEquals(2, parts.size)
        assertFalse(parts.any { it.contains('=') })
        assertFalse(payloadText.contains('\n'))
        assertFalse(payloadText.contains("usr_sensitive_internal_identifier"))
        assertEquals(
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
            ),
            payload.propertyNames().asSequence().toList(),
        )
        assertEquals(1, payload.path("schemaVersion").intValue())
        assertEquals("v1", payload.path("keyVersion").stringValue())
        assertEquals("principles", payload.path("route").stringValue())
        assertTrue(payload.path("subjectBinding").stringValue().isNotBlank())
        assertTrue(payload.path("filters").isObject)
        assertEquals(0, payload.path("filters").size())
        assertEquals("UPDATED_AT_DESC", payload.path("sort").stringValue())
        assertEquals(50, payload.path("size").intValue())
        assertEquals(issuedAt.epochSecond, payload.path("issuedAt").longValue())
        assertEquals(issuedAt.epochSecond + 900, payload.path("expiresAt").longValue())
        assertEquals("prc_0123456789abcdef0123456789abcdef", payload.path("lastPrincipleId").stringValue())
    }

    @Test
    fun `history cursor binds route resource and last version`() {
        val issuedAt = Instant.parse("2030-01-02T03:04:05Z")
        val token =
            codec(issuedAt).encodeHistory(
                userId = "usr_1",
                principleId = "prc_0123456789abcdef0123456789abcdef",
                size = 25,
                sort = "VERSION_ASC",
                version = 7,
            )
        val payload =
            objectMapper.readTree(
                Base64.getUrlDecoder().decode(token.substringBefore('.')),
            )

        assertEquals("principle-versions", payload.path("route").stringValue())
        assertEquals("prc_0123456789abcdef0123456789abcdef", payload.path("principleId").stringValue())
        assertEquals(7, payload.path("lastVersion").intValue())
        assertFalse(payload.has("lastUpdatedAt"))
        assertFalse(payload.has("lastPrincipleId"))
    }

    @Test
    fun `cursor rejects expiry and issuedAt beyond the allowed future skew`() {
        val now = Instant.parse("2030-01-02T03:04:05Z")
        val expired =
            codec(now).encodeOwner(
                userId = "usr_1",
                size = 50,
                sort = "UPDATED_AT_DESC",
                updatedAt = OffsetDateTime.ofInstant(now, ZoneOffset.UTC),
                principleId = "prc_0123456789abcdef0123456789abcdef",
            )
        assertThrows(InvalidPrincipleCursorException::class.java) {
            codec(now.plusSeconds(901)).decodeOwner(expired, "usr_1", 50, "UPDATED_AT_DESC")
        }

        val future =
            codec(now.plusSeconds(61)).encodeHistory(
                userId = "usr_1",
                principleId = "prc_0123456789abcdef0123456789abcdef",
                size = 50,
                sort = "VERSION_DESC",
                version = 2,
            )
        assertThrows(InvalidPrincipleCursorException::class.java) {
            codec(now).decodeHistory(
                future,
                "usr_1",
                "prc_0123456789abcdef0123456789abcdef",
                50,
                "VERSION_DESC",
            )
        }
    }

    private fun codec(now: Instant): PrincipleCursorCodec =
        PrincipleCursorCodec(
            properties = properties,
            catalog = catalog,
            principleClock = Clock.fixed(now, ZoneOffset.UTC),
        )
}
