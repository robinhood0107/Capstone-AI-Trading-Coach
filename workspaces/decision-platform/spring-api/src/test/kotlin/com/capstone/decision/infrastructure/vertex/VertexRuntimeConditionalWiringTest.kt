package com.capstone.decision.infrastructure.vertex

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.boot.test.context.TestConfiguration
import org.springframework.boot.test.context.runner.ApplicationContextRunner
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Import

class VertexRuntimeConditionalWiringTest {
    private val contextRunner =
        ApplicationContextRunner()
            .withUserConfiguration(VertexRuntimeConfiguration::class.java)

    @Test
    fun `disabled Vertex excludes credential provider from the retrieval-only process`() {
        contextRunner.run { context ->
            assertThat(context.getBeansOfType(PreS5VertexCredentialProvider::class.java)).isEmpty()
        }
    }

    @Test
    fun `enabled Vertex injects the explicitly selected production constructor`() {
        contextRunner
            .withPropertyValues("app.rag-v2.vertex.enabled=true")
            .run { context ->
                assertThat(context.getBeansOfType(PreS5VertexCredentialProvider::class.java)).hasSize(1)
            }
    }

    @TestConfiguration(proxyBeanMethods = false)
    @EnableConfigurationProperties(RagV2VertexProperties::class)
    @Import(PreS5VertexCredentialProvider::class)
    internal class VertexRuntimeConfiguration {
        @Bean
        fun tokenExecutor(): PreS5VertexTokenExecutor =
            object : PreS5VertexTokenExecutor {
                override fun execute(request: PreS5VertexTokenRequest): PreS5VertexTokenResponse =
                    error("Conditional wiring test must not issue an OAuth request.")
            }
    }
}
