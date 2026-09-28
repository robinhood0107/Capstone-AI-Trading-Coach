package com.capstone.decision.application.strongllm

import com.capstone.decision.TestVertexServiceAccount
import com.capstone.decision.application.security.ActorRlsScopePort
import io.mockk.mockk
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.support.StaticListableBeanFactory
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.mock.env.MockEnvironment

/**
 * FULL 은 사용자 자기 Vertex 서비스 계정만 받는다. 저장소에 닿기 전에 모양을 닫으므로 DB 없이 판정한다:
 * 규칙을 통과하면 저장소 부재(STRONG_LLM_SETTINGS_UNAVAILABLE)까지 가고, 어기면 그 전에 400 부류로 끝난다.
 */
class StrongLlmSettingsFullProductRuleTest {
    private val noDatabase = StaticListableBeanFactory().getBeanProvider(NamedParameterJdbcTemplate::class.java)
    private val full = MockEnvironment().apply { setActiveProfiles("mars-full") }
    private val local = MockEnvironment()
    private val sa = TestVertexServiceAccount.base64

    private fun service(environment: MockEnvironment) =
        StrongLlmSettingsService(noDatabase, mockk<ActorRlsScopePort>(relaxed = true), mockk(relaxed = true), environment)

    private fun command(
        provider: String = "vertex",
        apiKey: String? = null,
        fallbackProvider: String? = null,
    ) = PutStrongLlmSettingsCommand(
        provider = provider,
        fallbackProvider = fallbackProvider,
        modelId = null,
        fallbackModelId = null,
        baseUrl = null,
        fallbackBaseUrl = null,
        answerLanguage = "ko",
        dailyGenerateCallCap = 50,
        apiKey = apiKey,
        fallbackApiKey = null,
        aiJudgementEnabled = true,
    )

    @Test
    fun `FULL accepts a Vertex service account and AI toggles without a key`() {
        assertThatThrownBy { service(full).put("usr_a", command(apiKey = sa)) }
            .isInstanceOf(StrongLlmSettingsUnavailableException::class.java)
        assertThatThrownBy { service(full).put("usr_a", command()) }
            .isInstanceOf(StrongLlmSettingsUnavailableException::class.java)
        assertThatThrownBy { service(full).put("usr_a", command(apiKey = "")) }
            .isInstanceOf(StrongLlmSettingsUnavailableException::class.java)
    }

    @Test
    fun `FULL refuses other providers, fallback providers and API keys`() {
        assertThatThrownBy { service(full).put("usr_a", command(provider = "openai", apiKey = "sk-FAKEFAKEFAKE")) }
            .isInstanceOf(IllegalArgumentException::class.java)
        assertThatThrownBy { service(full).put("usr_a", command(fallbackProvider = "vertex")) }
            .isInstanceOf(IllegalArgumentException::class.java)
        assertThatThrownBy { service(full).put("usr_a", command(apiKey = "AIzaSyFAKEFAKEFAKEFAKE")) }
            .isInstanceOf(IllegalArgumentException::class.java)
    }

    @Test
    fun `LOCAL keeps accepting every provider it accepted before`() {
        assertThatThrownBy { service(local).put("usr_a", command(provider = "openai", apiKey = "sk-FAKEFAKEFAKE")) }
            .isInstanceOf(StrongLlmSettingsUnavailableException::class.java)
    }
}
