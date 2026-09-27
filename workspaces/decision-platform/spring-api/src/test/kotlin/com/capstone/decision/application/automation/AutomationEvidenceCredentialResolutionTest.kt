package com.capstone.decision.application.automation

import com.capstone.decision.application.security.ActorRlsScopePort
import com.capstone.decision.application.strongllm.StrongLlmCredentialPort
import com.capstone.decision.application.strongllm.StrongLlmSealedCredential
import io.mockk.every
import io.mockk.mockk
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.support.StaticListableBeanFactory
import org.springframework.jdbc.core.RowMapper
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.transaction.PlatformTransactionManager
import tools.jackson.databind.json.JsonMapper
import java.util.Base64

/** AI 검토 한 번의 자격증명 선택: 자기 키 → (허용된 배포에서만) 운영자 → 부르지 않음. */
class AutomationEvidenceCredentialResolutionTest {
    private val jdbc = mockk<NamedParameterJdbcTemplate>()
    private val port = mockk<StrongLlmCredentialPort>()
    private val sealed =
        StrongLlmSealedCredential("kek-v1", ByteArray(12), ByteArray(32), ByteArray(16), ByteArray(12), ByteArray(8), ByteArray(16), "")
    private val ownServiceAccount =
        Base64.getEncoder().encodeToString(
            (
                """{"type":"service_account","project_id":"p","private_key_id":"abcd1234",""" +
                    """"private_key":"-----BEGIN PRIVATE KEY-----\nA\n-----END PRIVATE KEY-----\n",""" +
                    """"client_email":"a@p.iam.gserviceaccount.com","token_uri":"https://oauth2.googleapis.com/token"}"""
            ).toByteArray(),
        )
    private val settings =
        AutomationEvidenceSettings("a".repeat(64), "vertex", null, null, null, null, null, "ko", 50, true, "low")

    private fun service(operatorFallback: Boolean) =
        AutomationEvidenceService(
            StaticListableBeanFactory().getBeanProvider(NamedParameterJdbcTemplate::class.java),
            mockk<ActorRlsScopePort>(relaxed = true),
            StaticListableBeanFactory().getBeanProvider(AutomationEvidenceProvider::class.java),
            JsonMapper.builder().build(),
            StaticListableBeanFactory(
                mapOf("transactionManager" to mockk<PlatformTransactionManager>(relaxed = true)),
            ).getBeanProvider(PlatformTransactionManager::class.java),
            port,
            AutomationAiProviderPolicy(operatorFallback),
        )

    private fun storedKey(present: Boolean) {
        every { jdbc.query(any<String>(), any<Map<String, *>>(), any<RowMapper<StrongLlmSealedCredential>>()) } returns
            if (present) listOf(sealed) else emptyList()
    }

    @Test
    fun `a registered own service account is used even when the operator fallback is on`() {
        storedKey(present = true)
        every { port.open("usr_a", "PRIMARY", sealed) } returns ownServiceAccount.toByteArray()
        service(operatorFallback = true).resolveCredential(jdbc, "usr_a", settings).use {
            assertThat(it.source).isEqualTo(AutomationAiCredentialSource.OWNER)
            assertThat(String(requireNotNull(it.ownerServiceAccountB64()))).isEqualTo(ownServiceAccount)
        }
    }

    @Test
    fun `without an own key the operator Vertex is used only when the deployment allows it`() {
        storedKey(present = false)
        service(operatorFallback = true).resolveCredential(jdbc, "usr_a", settings).use {
            assertThat(it.source).isEqualTo(AutomationAiCredentialSource.OPERATOR)
            assertThat(it.ownerServiceAccountB64()).isNull()
        }
        assertThatThrownBy { service(operatorFallback = false).resolveCredential(jdbc, "usr_a", settings) }
            .isInstanceOf(AutomationEvidenceUnavailableException::class.java)
    }

    @Test
    fun `a stored key that is not a service account never reaches the agent`() {
        storedKey(present = true)
        every { port.open("usr_a", "PRIMARY", sealed) } returns "sk-FAKEFAKEFAKE".toByteArray()
        service(operatorFallback = true).resolveCredential(jdbc, "usr_a", settings).use {
            assertThat(it.source).isEqualTo(AutomationAiCredentialSource.OPERATOR)
        }
        assertThatThrownBy { service(operatorFallback = false).resolveCredential(jdbc, "usr_a", settings) }
            .isInstanceOf(AutomationEvidenceUnavailableException::class.java)
    }
}
