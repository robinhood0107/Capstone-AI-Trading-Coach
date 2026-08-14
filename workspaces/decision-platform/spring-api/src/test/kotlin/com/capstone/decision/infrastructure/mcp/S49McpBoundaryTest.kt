package com.capstone.decision.infrastructure.mcp

import com.capstone.decision.infrastructure.rag.RagV2UnavailableVertexGenerationAdapter
import jakarta.servlet.FilterChain
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import org.springframework.ai.mcp.annotation.McpTool
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperties
import org.springframework.core.annotation.AnnotatedElementUtils
import org.springframework.mock.web.MockHttpServletRequest
import org.springframework.mock.web.MockHttpServletResponse
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
