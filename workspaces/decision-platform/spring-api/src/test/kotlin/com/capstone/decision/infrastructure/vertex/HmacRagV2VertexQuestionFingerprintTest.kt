package com.capstone.decision.infrastructure.vertex

import com.capstone.decision.application.rag.RagAnswerMode
import com.capstone.decision.application.rag.RagAskCommand
import com.capstone.decision.infrastructure.rag.RagGuardHistoryProperties
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test

class HmacRagV2VertexQuestionFingerprintTest {
    private val fingerprint =
        HmacRagV2VertexQuestionFingerprint(
            RagGuardHistoryProperties(providerUsageHmacKey = "v".repeat(32)),
        )

    @Test
    fun `prepared scope HMAC binds the complete parsed ask command without retaining raw content`() {
        val base =
            RagAskCommand(
                question = "Explain the cited model limitation.",
                answerMode = RagAnswerMode.CONCISE,
                relatedSymbols = listOf("005930"),
                topics = listOf("FINANCIAL_ENGINEERING"),
            )

        val reference = fingerprint.fingerprint("usr_demo_user", base)

        assertThat(reference).matches("[0-9a-f]{64}")
        assertThat(fingerprint.fingerprint("usr_demo_user", base.copy())).isEqualTo(reference)
        assertThat(
            fingerprint.fingerprint(
                "usr_demo_user",
                base.copy(relatedSymbols = listOf("000660")),
            ),
        ).isNotEqualTo(reference)
        assertThat(
            fingerprint.fingerprint(
                "usr_demo_user",
                base.copy(topics = listOf("RISK")),
            ),
        ).isNotEqualTo(reference)
        assertThat(
            fingerprint.fingerprint(
                "usr_demo_user",
                base.copy(question = "Explain a different cited limitation."),
            ),
        ).isNotEqualTo(reference)
    }
}
