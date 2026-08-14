package com.capstone.decision.infrastructure.mcp

import com.capstone.decision.infrastructure.rag.RagV2UnavailableVertexGenerationAdapter
import jakarta.servlet.FilterChain
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatCode
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import org.springframework.ai.mcp.annotation.McpTool
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperties
import org.springframework.core.annotation.AnnotatedElementUtils
import org.springframework.mock.web.MockHttpServletRequest
import org.springframework.mock.web.MockHttpServletResponse
import org.springframework.security.config.ObjectPostProcessor
import org.springframework.security.config.annotation.authentication.builders.AuthenticationManagerBuilder
import org.springframework.security.config.annotation.web.builders.HttpSecurity
import org.springframework.security.web.context.SecurityContextHolderFilter
import java.nio.file.Files
import java.nio.file.Path
import kotlin.reflect.full.declaredFunctions
import kotlin.reflect.jvm.javaMethod

class S49McpBoundaryTest {
    @Test
    fun `Strong LLM activation excludes the unavailable generation adapter`() {
        val conditions =
            AnnotatedElementUtils
                .findMergedAnnotation(RagV2UnavailableVertexGenerationAdapter::class.java, ConditionalOnProperties::class.java)
                ?.value
                ?.toList()
                .orEmpty()

        assertThat(conditions.map { it.name.toList() to it.havingValue }).contains(
            listOf("app.rag-v2.vertex.enabled") to "false",
            listOf("app.s4-9.strong-llm.enabled") to "false",
        )
    }

    @Test
    fun `MCP surface exposes exactly five provider neutral tools without owner argument`() {
        val methods =
            CapstoneMcpTools::class.declaredFunctions.mapNotNull { function ->
                function.javaMethod?.getAnnotation(McpTool::class.java)?.let { it.name to function }
            }

        assertThat(methods.map { it.first }).containsExactlyInAnyOrder(
            "capstone_rag_search",
            "capstone_web_search",
            "capstone_web_read",
            "capstone_answer_validate",
            "capstone_answer_save",
        )
        assertThat(
            methods.flatMap {
                it.second.parameters.mapNotNull { parameter ->
                    parameter.name
                }
            },
        ).doesNotContain("ownerId", "ownerUserId")
    }

    @Test
    fun `resource indicator filter requires the exact MCP audience`() {
        val filter = McpResourceIndicatorFilter("https://api.example.com/mcp")
        val chain = FilterChain { _, _ -> throw AssertionError("invalid resource reached the authorization server") }
        val invalid = MockHttpServletRequest("GET", "/oauth2/authorize")
        val invalidResponse = MockHttpServletResponse()

        filter.doFilter(invalid, invalidResponse, chain)

        assertThat(invalidResponse.status).isEqualTo(400)

        var reached = false
        val valid =
            MockHttpServletRequest("GET", "/oauth2/authorize").apply {
                addParameter("resource", "https://api.example.com/mcp")
            }
        filter.doFilter(valid, MockHttpServletResponse()) { _, _ -> reached = true }
        assertThat(reached).isTrue()

        val token =
            MockHttpServletRequest("POST", "/oauth2/token").apply {
                addParameter("resource", "https://api.example.com/mcp", "https://evil.example/mcp")
            }
        val tokenResponse = MockHttpServletResponse()
        filter.doFilter(token, tokenResponse, chain)
        assertThat(tokenResponse.status).isEqualTo(400)
    }

    @Test
    fun `resource indicator uses a Spring Security registered filter anchor`() {
        val postProcessor = ObjectPostProcessor.identity<Any>()
        val http = HttpSecurity(postProcessor, AuthenticationManagerBuilder(postProcessor), mutableMapOf())

        assertThatCode {
            http.addFilterAfter(
                McpResourceIndicatorFilter("https://api.example.com/mcp"),
                SecurityContextHolderFilter::class.java,
            )
        }.doesNotThrowAnyException()
    }

    @Test
    fun `S4 9 runtime accepts PostgreSQL void success values instead of SQL null`() {
        val paths =
            listOf(
                "infrastructure/mcp/HashingMcpOAuthAuthorizationService.kt",
                "infrastructure/mcp/McpAnswerValidationReceiptRegistry.kt",
                "infrastructure/mcp/JdbcS49WebEvidenceMetadataRepository.kt",
                "infrastructure/vertex/JdbcS49StrongLlmUsageLedger.kt",
            ).map { Path.of("src/main/kotlin/com/capstone/decision").resolve(it) }

        paths.forEach { path ->
            val source = Files.readString(path)
            assertThat(source).doesNotContain(") IS NULL")
            assertThat(source).contains(") IS NOT NULL")
        }
    }

    @Test
    fun `MCP access token signing stays bound to the configured P 256 JWK`() {
        val source =
            Files.readString(
                Path.of("src/main/kotlin/com/capstone/decision/infrastructure/mcp/McpOAuthSecurityConfig.kt"),
            )

        assertThat(source).contains(
            ".idTokenSignatureAlgorithm(SignatureAlgorithm.ES256)",
            "context.jwsHeader.algorithm(SignatureAlgorithm.ES256)",
        )
    }

    @Test
    fun `OAuth revocation persists an invalidated refresh token as a revoked family`() {
        val source =
            Files.readString(
                Path.of("src/main/kotlin/com/capstone/decision/infrastructure/mcp/HashingMcpOAuthAuthorizationService.kt"),
            )

        assertThat(source).contains(
            "if (refreshState.isInvalidated)",
            "revokeRefreshFamily(refresh)",
            "revoke_s4_9_mcp_refresh_token_family",
        )
    }

    @Test
    fun `mode budgets can be lowered but never exceed absolute caps`() {
        val valid = RagWebToolProperties(receiptHmacKey = "k".repeat(32))
        assertThat(valid.budget("CONCISE")).isEqualTo(RagToolBudget(1, 3, 3, 3))
        assertThat(valid.budget("DETAILED")).isEqualTo(RagToolBudget(2, 6, 3, 3))

        assertThatThrownBy {
            RagWebToolProperties(absoluteMaxSearches = 4, receiptHmacKey = "k".repeat(32)).validate()
        }.isInstanceOf(IllegalArgumentException::class.java)
        assertThatThrownBy {
            RagWebToolProperties(detailedMaxReads = 9, absoluteMaxReads = 8, receiptHmacKey = "k".repeat(32)).validate()
        }.isInstanceOf(IllegalArgumentException::class.java)
    }
}
