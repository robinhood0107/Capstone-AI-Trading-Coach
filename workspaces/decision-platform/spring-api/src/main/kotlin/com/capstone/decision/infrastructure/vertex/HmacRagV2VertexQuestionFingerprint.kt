package com.capstone.decision.infrastructure.vertex

import com.capstone.decision.application.rag.RagAskCommand
import com.capstone.decision.application.rag.RagV2VertexQuestionFingerprintPort
import com.capstone.decision.infrastructure.rag.RagGuardHistoryProperties
import org.springframework.stereotype.Component
import java.nio.charset.StandardCharsets
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * preparation, activation packet, usage lease가 같은 full ask binding을 사용하도록 HMAC 계산을 한 곳에 둔다.
 * raw ask content는 persistence에 기록하지 않으며, purpose-separated key는 local application configuration에서만 읽는다.
 */
@Component
internal class HmacRagV2VertexQuestionFingerprint(
    private val ragProperties: RagGuardHistoryProperties,
) : RagV2VertexQuestionFingerprintPort {
    override fun fingerprint(
        ownerUserId: String,
        command: RagAskCommand,
    ): String {
        val key = ragProperties.providerUsageHmacKey.toByteArray(StandardCharsets.UTF_8)
        val message =
            listOf(
                "rag-v2-vertex-ask-fingerprint/v2",
                ownerUserId,
                command.question,
                command.answerMode.name,
                command.relatedSymbols.joinToString("\u001f"),
                command.topics.joinToString("\u001f"),
            ).joinToString("\u0000").toByteArray(StandardCharsets.UTF_8)
        return try {
            val mac = Mac.getInstance("HmacSHA256")
            mac.init(SecretKeySpec(key, "HmacSHA256"))
            mac.doFinal(message).joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
        } finally {
            key.fill(0)
            message.fill(0)
        }
    }
}
