package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.extension.ExtendWith
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.boot.test.system.CapturedOutput
import org.springframework.boot.test.system.OutputCaptureExtension
import org.springframework.http.MediaType
import org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.get
import org.springframework.test.web.servlet.post
import org.springframework.test.web.servlet.setup.DefaultMockMvcBuilder
import org.springframework.test.web.servlet.setup.MockMvcBuilders
import org.springframework.web.context.WebApplicationContext
import tools.jackson.databind.ObjectMapper

@SpringBootTest(
    properties = [
        "spring.autoconfigure.exclude=org.springframework.boot.jdbc.autoconfigure.DataSourceAutoConfiguration,org.springframework.boot.hibernate.autoconfigure.HibernateJpaAutoConfiguration,org.springframework.boot.data.jpa.autoconfigure.DataJpaRepositoriesAutoConfiguration,org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration",
    ],
)
@ExtendWith(OutputCaptureExtension::class)
class RequestLoggingIntegrationTest(
    @Autowired private val webApplicationContext: WebApplicationContext,
    @Autowired private val objectMapper: ObjectMapper,
) : SpringApiIntegrationTestBase() {
    private lateinit var mockMvc: MockMvc

    @BeforeEach
    fun setUpMockMvc() {
        mockMvc =
            MockMvcBuilders
                .webAppContextSetup(webApplicationContext)
                .apply<DefaultMockMvcBuilder>(springSecurity())
                .build()
    }

    @Test
    fun `json logs include request id from mdc`(output: CapturedOutput) {
        val token =
            mockMvc
                .post("/api/v1/auth/login") {
                    contentType = MediaType.APPLICATION_JSON
                    content = """{"username":"demo-user","password":"${userPassword()}"}"""
                    header("X-Request-Id", "req-log-login")
                }.andReturn()
                .response
                .contentAsString
                .let { objectMapper.readTree(it).at("/data/accessToken").stringValue() }

        mockMvc
            .get("/api/v1/system/health") {
                bearer(token)
                header("X-Request-Id", "req-log-health")
            }.andReturn()

        assertTrue(output.out.contains(""""requestId":"req-log-health""""))
    }
}
