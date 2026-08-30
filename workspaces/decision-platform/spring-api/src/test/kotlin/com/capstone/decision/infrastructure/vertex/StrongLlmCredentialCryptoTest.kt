package com.capstone.decision.infrastructure.vertex

import com.capstone.decision.application.strongllm.StrongLlmCredentialCorruptedException
import com.capstone.decision.infrastructure.rag.InMemoryRagHistoryKekProvider
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import java.nio.charset.StandardCharsets

class StrongLlmCredentialCryptoTest {
    private val provider =
        InMemoryRagHistoryKekProvider(
            currentVersion = "kek-v1",
            keys = mapOf("kek-v1" to ByteArray(32) { it.toByte() }, "kek-v2" to ByteArray(32) { 7 }),
        )
    private val crypto = StrongLlmCredentialCrypto(provider)
    private val owner = "usr_demo_user"

    @Test
    fun `a sealed key comes back only for the owner and slot that sealed it`() {
        val sealed = crypto.seal(owner, "PRIMARY", "sk-test-1234567890")

        assertThat(String(crypto.open(owner, "PRIMARY", sealed), StandardCharsets.UTF_8))
            .isEqualTo("sk-test-1234567890")
        // 봉투를 다른 슬롯이나 다른 소유자의 행에 옮겨 붙여도 열리지 않는다. AAD가 그 둘을 묶는다.
        assertThatThrownBy { crypto.open(owner, "FALLBACK", sealed) }
            .isInstanceOf(StrongLlmCredentialCorruptedException::class.java)
        assertThatThrownBy { crypto.open("usr_other_user", "PRIMARY", sealed) }
            .isInstanceOf(StrongLlmCredentialCorruptedException::class.java)
    }

    @Test
    fun `only the last four characters leave the seal`() {
        val sealed = crypto.seal(owner, "PRIMARY", "sk-test-abcdWXYZ")

        assertThat(sealed.keyLast4).isEqualTo("WXYZ")
        // 평문이 봉투 어디에도 남지 않는다. 화면이 보여줄 수 있는 것은 마지막 네 글자뿐이다.
        val bytes = sealed.keyCiphertext + sealed.wrappedDek + sealed.wrapTag + sealed.keyTag
        assertThat(String(bytes, StandardCharsets.ISO_8859_1)).doesNotContain("sk-test-abcd")
    }

    @Test
    fun `a tampered envelope fails closed without saying which part was wrong`() {
        val sealed = crypto.seal(owner, "PRIMARY", "sk-test-1234567890")
        val tampered = sealed.copy(keyCiphertext = sealed.keyCiphertext.copyOf().also { it[0] = (it[0] + 1).toByte() })

        assertThatThrownBy { crypto.open(owner, "PRIMARY", tampered) }
            .isInstanceOf(StrongLlmCredentialCorruptedException::class.java)
            .hasMessage("STRONG_LLM_CREDENTIAL_CORRUPTED")
    }

    @Test
    fun `a key sealed under an older KEK still opens after the current version moves`() {
        val sealed = crypto.seal(owner, "PRIMARY", "sk-test-1234567890")
        val rotated =
            StrongLlmCredentialCrypto(
                InMemoryRagHistoryKekProvider(
                    currentVersion = "kek-v2",
                    keys = mapOf("kek-v1" to ByteArray(32) { it.toByte() }, "kek-v2" to ByteArray(32) { 7 }),
                ),
            )

        assertThat(String(rotated.open(owner, "PRIMARY", sealed), StandardCharsets.UTF_8))
            .isEqualTo("sk-test-1234567890")
    }

    @Test
    fun `a key too short to be real is refused before it is stored`() {
        assertThatThrownBy { crypto.seal(owner, "PRIMARY", "short") }
            .isInstanceOf(IllegalArgumentException::class.java)
    }
}
