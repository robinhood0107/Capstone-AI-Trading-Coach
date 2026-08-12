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
    fun `disabled Vertex excludes service-account OAuth providers from the retrieval-only process`() {
        contextRunner.run { context ->
            assertThat(context.getBeansOfType(PreS5VertexServiceAccountCredentialProvider::class.java)).isEmpty()
            assertThat(context.getBeansOfType(PreS5VertexServiceAccountOAuthProvider::class.java)).isEmpty()
        }
    }

    @Test
    fun `enabled Vertex injects the service-account OAuth production boundary`() {
        contextRunner
            .withPropertyValues("app.rag-v2.vertex.enabled=true")
            .run { context ->
                assertThat(context.getBeansOfType(PreS5VertexServiceAccountCredentialProvider::class.java)).hasSize(1)
                assertThat(context.getBeansOfType(PreS5VertexOAuthTokenExecutor::class.java)).hasSize(1)
                assertThat(context.getBeansOfType(PreS5VertexServiceAccountOAuthProvider::class.java)).hasSize(1)
            }
    }

    @TestConfiguration(proxyBeanMethods = false)
    @EnableConfigurationProperties(RagV2VertexProperties::class)
    @Import(
        PreS5VertexServiceAccountCredentialProvider::class,
        JdkPreS5VertexOAuthTokenExecutor::class,
        PreS5VertexServiceAccountOAuthProvider::class,
    )
    internal class VertexRuntimeConfiguration
}
