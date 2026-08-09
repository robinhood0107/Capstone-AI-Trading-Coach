package com.capstone.decision.infrastructure.vertex

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.boot.test.context.TestConfiguration
import org.springframework.boot.test.context.runner.ApplicationContextRunner
import org.springframework.context.annotation.Import

class VertexRuntimeConditionalWiringTest {
    private val contextRunner =
        ApplicationContextRunner()
            .withUserConfiguration(VertexRuntimeConfiguration::class.java)

    @Test
    fun `disabled Vertex excludes API key provider from the retrieval-only process`() {
        contextRunner.run { context ->
            assertThat(context.getBeansOfType(PreS5VertexApiKeyProvider::class.java)).isEmpty()
        }
    }

    @Test
    fun `enabled Vertex injects the API-key-only production constructor`() {
        contextRunner
            .withPropertyValues("app.rag-v2.vertex.enabled=true")
            .run { context ->
                assertThat(context.getBeansOfType(PreS5VertexApiKeyProvider::class.java)).hasSize(1)
            }
    }

    @TestConfiguration(proxyBeanMethods = false)
    @EnableConfigurationProperties(RagV2VertexProperties::class)
    @Import(PreS5VertexApiKeyProvider::class)
    internal class VertexRuntimeConfiguration
}
