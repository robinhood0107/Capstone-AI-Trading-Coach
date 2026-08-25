package com.capstone.decision.infrastructure.mcp

import jakarta.servlet.FilterChain
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import org.springframework.mock.web.MockHttpServletRequest
import org.springframework.mock.web.MockHttpServletResponse

class McpRefreshClaimCleanupFilterTest {
    @Test
    fun `claim is empty at request entry and cleared after failure`() {
        val claims = McpRefreshClaimContext()
        val claim =
            McpRefreshClaim(
                "a".repeat(64),
                "mcp_client",
                "usr_owner",
                1,
                "https://resource",
                setOf("mcp:rag.public"),
            )
        claims.bind(claim)
        val chain =
            FilterChain { _, _ ->
                assertThat(claims.optional()).isNull()
                claims.bind(claim)
                throw IllegalStateException("downstream failure")
            }

        assertThatThrownBy {
            McpRefreshClaimCleanupFilter(claims).doFilter(
                MockHttpServletRequest(),
                MockHttpServletResponse(),
                chain,
            )
        }.isInstanceOf(IllegalStateException::class.java)
        assertThat(claims.optional()).isNull()
    }
}
