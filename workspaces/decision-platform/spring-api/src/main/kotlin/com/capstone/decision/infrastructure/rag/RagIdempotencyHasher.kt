package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagAskCommand
import com.capstone.decision.application.rag.RagIdempotencyIdentity
import com.capstone.decision.application.rag.RagIdempotencyPort
import com.capstone.decision.domain.risk.CanonicalJson
import org.springframework.stereotype.Component
import java.nio.charset.StandardCharsets
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

@Component
class RagIdempotencyHasher(
    private val properties: RagGuardHistoryProperties,
) : RagIdempotencyPort {
    /**
     * raw key는 이 호출 안에서만 사용하며 scope와 canonical request를 서로 다른 key/purpose로 HMAC한다.
     */
    override fun identity(
        ownerUserId: String,
        rawKey: String,
        command: RagAskCommand,
    ): RagIdempotencyIdentity {
        require(ownerUserId.isNotBlank() && ownerUserId.length <= 128)
        require(IDEMPOTENCY_KEY.matches(rawKey))
        val scope =
            hmac(
                key = properties.idempotencyScopeHmacKey,
                parts =
                    listOf(
                        RagGuardHistoryProperties.SCOPE_PURPOSE,
                        ownerUserId,
                        "POST",
                        "/api/v1/rag/ask",
                        rawKey,
                    ),
            )
        val canonicalRequest =
            CanonicalJson.encode(
                mapOf(
                    "answerMode" to command.answerMode.name,
                    "question" to command.question,
                    "relatedSymbols" to command.relatedSymbols.sorted(),
                    "topics" to command.topics.sorted(),
                ),
            )
        val requestFingerprint =
            hmac(
                key = properties.requestFingerprintHmacKey,
                parts =
                    listOf(
                        RagGuardHistoryProperties.REQUEST_PURPOSE,
                        canonicalRequest,
                    ),
            )
        return RagIdempotencyIdentity(
            scopeHmac = scope,
            requestFingerprint = requestFingerprint,
        )
    }

    private fun hmac(
        key: String,
        parts: List<String>,
    ): String {
        val keyBytes = key.toByteArray(StandardCharsets.UTF_8)
        val messageBytes = parts.joinToString("\u0000").toByteArray(StandardCharsets.UTF_8)
        return try {
            val mac = Mac.getInstance("HmacSHA256")
            mac.init(SecretKeySpec(keyBytes, "HmacSHA256"))
            mac.doFinal(messageBytes).joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
        } finally {
            keyBytes.fill(0)
            messageBytes.fill(0)
        }
    }

    private companion object {
        val IDEMPOTENCY_KEY = Regex("^[A-Za-z0-9._~-]{16,128}$")
    }
}
