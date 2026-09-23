package com.capstone.decision.api.rag

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.application.rag.RagGenerationStatus
import com.capstone.decision.application.rag.RagRateLimitPort
import com.capstone.decision.application.rag.RagRateLimitedException
import com.capstone.decision.application.rag.RagV2VertexGenerationCommand
import com.capstone.decision.application.rag.RagV2VertexGenerationResult
import com.capstone.decision.infrastructure.grpc.DEMO_INTERNAL_OWNER_USER_ID
import com.capstone.decision.infrastructure.grpc.GrpcStrongLlmGenerationAdapter
import io.mockk.Runs
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.springframework.mock.web.MockHttpServletRequest
import org.springframework.mock.web.MockHttpServletResponse
import tools.jackson.databind.json.JsonMapper

class DemoAgentControllerTest {
    @Test
    fun `anonymous question uses only fixed public evidence and returns no history identifier`() {
        val generation = mockk<GrpcStrongLlmGenerationAdapter>()
        val rate = mockk<RagRateLimitPort>()
        val command = slot<RagV2VertexGenerationCommand>()
        every { rate.acquire(DEMO_INTERNAL_OWNER_USER_ID) } just Runs
        every { generation.generate(capture(command)) } returns
            RagV2VertexGenerationResult(
                generationStatus = RagGenerationStatus.ANSWERED,
                answer = "분산은 위험 집중을 줄일 수 있습니다.",
                citationIds = listOf("cit_1"),
                failureCode = "",
            )
        val request = MockHttpServletRequest("POST", "/api/v1/demo/agent/ask")
        request.addHeader("X-Request-Id", "req_demo_unit_0001")
        val browserResponse = MockHttpServletResponse()

        val response =
            DemoAgentController(generation, rate).ask(
                """{"questionId":"diversification"}""",
                request,
                browserResponse,
            )

        assertTrue(response.success)
        assertEquals(DEMO_INTERNAL_OWNER_USER_ID, command.captured.ownerUserId)
        assertEquals("분산투자는 위험을 어떻게 줄이나요?", command.captured.question)
        assertTrue(command.captured.requestId != response.requestId)
        assertTrue(command.captured.evidence.all { !it.ownerPrivate })
        assertEquals(3, command.captured.evidence.size)
        assertEquals(
            "https://www.investor.gov/introduction-investing/getting-started/asset-allocation",
            response.data
                ?.citations
                ?.single()
                ?.url,
        )
        val publicJson = JsonMapper.builder().build().writeValueAsString(response)
        assertTrue(!publicJson.contains("historyId") && !publicJson.contains("question"))
        assertEquals("no-store", browserResponse.getHeader("Cache-Control"))
        verify(exactly = 1) { rate.acquire(DEMO_INTERNAL_OWNER_USER_ID) }
    }

    @Test
    fun `unknown fields and duplicate JSON keys stop before rate or provider use`() {
        val generation = mockk<GrpcStrongLlmGenerationAdapter>()
        val rate = mockk<RagRateLimitPort>()
        val controller = DemoAgentController(generation, rate)
        val request = MockHttpServletRequest("POST", "/api/v1/demo/agent/ask")
        val rejectedBodies =
            listOf(
                """{"questionId":"diversification","owner":"usr_other"}""",
                """{"questionId":"diversification","questionId":"asset_allocation"}""",
                """{"questionId":"stock_recommendation"}""",
            )
        for (body in rejectedBodies) {
            val error =
                assertThrows(ApiException::class.java) {
                    controller.ask(body, request, MockHttpServletResponse())
                }
            assertEquals(ErrorCode.VALIDATION_ERROR, error.errorCode)
        }
        verify(exactly = 0) { rate.acquire(any()) }
        verify(exactly = 0) { generation.generate(any()) }
    }

    @Test
    fun `shared minute limit rejects before provider use`() {
        val generation = mockk<GrpcStrongLlmGenerationAdapter>()
        val rate = mockk<RagRateLimitPort>()
        every { rate.acquire(DEMO_INTERNAL_OWNER_USER_ID) } throws RagRateLimitedException()
        val request = MockHttpServletRequest("POST", "/api/v1/demo/agent/ask")

        val error =
            assertThrows(ApiException::class.java) {
                DemoAgentController(generation, rate).ask(
                    """{"questionId":"diversification"}""",
                    request,
                    MockHttpServletResponse(),
                )
            }

        assertEquals(ErrorCode.RATE_LIMITED, error.errorCode)
        verify(exactly = 0) { generation.generate(any()) }
    }

    @Test
    fun `daily operator cap reports a bounded public error`() {
        val generation = mockk<GrpcStrongLlmGenerationAdapter>()
        val rate = mockk<RagRateLimitPort>()
        every { rate.acquire(DEMO_INTERNAL_OWNER_USER_ID) } just Runs
        every { generation.generate(any()) } returns
            RagV2VertexGenerationResult(
                generationStatus = RagGenerationStatus.GENERATION_UNAVAILABLE,
                answer = null,
                citationIds = emptyList(),
                failureCode = "DEMO_AI_BUDGET_EXHAUSTED",
            )
        val request = MockHttpServletRequest("POST", "/api/v1/demo/agent/ask")

        val error =
            assertThrows(ApiException::class.java) {
                DemoAgentController(generation, rate).ask(
                    """{"questionId":"diversification"}""",
                    request,
                    MockHttpServletResponse(),
                )
            }

        assertEquals(ErrorCode.RATE_LIMITED, error.errorCode)
    }
}
