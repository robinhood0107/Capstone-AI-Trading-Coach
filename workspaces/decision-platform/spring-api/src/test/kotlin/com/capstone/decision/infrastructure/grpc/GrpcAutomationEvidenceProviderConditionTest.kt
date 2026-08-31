package com.capstone.decision.infrastructure.grpc

import com.capstone.decision.application.automation.AutomationEvidenceProvider
import com.capstone.decision.application.automation.FixtureAutomationEvidenceProvider
import com.capstone.decision.infrastructure.vertex.S49StrongLlmProperties
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.boot.test.context.runner.ApplicationContextRunner
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.context.annotation.Import
import tools.jackson.databind.ObjectMapper
import tools.jackson.databind.json.JsonMapper

class GrpcAutomationEvidenceProviderConditionTest {
    private val runner =
        ApplicationContextRunner()
            .withUserConfiguration(Config::class.java)
            .withPropertyValues(
                "app.s4-9.strong-llm.enabled=true",
                "app.p1.automation.evidence-fixture-enabled=false",
            )

    @Test
    fun `real provider is available when strong llm is enabled and fixture is off`() {
        runner.run { context ->
            val providers = context.getBeansOfType(AutomationEvidenceProvider::class.java)

            assertThat(providers).hasSize(1)
            assertThat(providers.values.single()).isInstanceOf(GrpcAutomationEvidenceProvider::class.java)
        }
    }

    @Test
    fun `fixture provider is primary when explicitly enabled`() {
        runner
            .withPropertyValues("app.p1.automation.evidence-fixture-enabled=true")
            .run { context ->
                val selected = context.getBean(AutomationEvidenceProvider::class.java)

                assertThat(selected).isInstanceOf(FixtureAutomationEvidenceProvider::class.java)
            }
    }

    @Configuration(proxyBeanMethods = false)
    @Import(GrpcAutomationEvidenceProvider::class, FixtureAutomationEvidenceProvider::class)
    internal class Config {
        @Bean
        fun strongLlmProperties() =
            S49StrongLlmProperties(
                enabled = true,
                modelId = "gemini-3.5-flash",
                localRoot = "/tmp/strong-llm",
            )

        @Bean
        fun grpcProperties() =
            StrongLlmAgentGrpcProperties(
                target = "127.0.0.1:50055",
                sharedSecret = "a".repeat(32),
            )

        @Bean
        fun objectMapper(): ObjectMapper = JsonMapper.builder().build()
    }
}
