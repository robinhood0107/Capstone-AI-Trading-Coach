package com.capstone.decision

import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.get
import org.springframework.test.web.servlet.setup.DefaultMockMvcBuilder
import org.springframework.test.web.servlet.setup.MockMvcBuilders
import org.springframework.web.context.WebApplicationContext

@SpringBootTest(
    properties = [
        "spring.autoconfigure.exclude=org.springframework.boot.jdbc.autoconfigure.DataSourceAutoConfiguration,org.springframework.boot.hibernate.autoconfigure.HibernateJpaAutoConfiguration,org.springframework.boot.data.jpa.autoconfigure.DataJpaRepositoriesAutoConfiguration,org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration",
    ],
)
class OpenApiConfigIntegrationTest(
    @Autowired private val webApplicationContext: WebApplicationContext,
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
    fun `openapi exposes bearer auth scheme`() {
        mockMvc
            .get("/v3/api-docs")
            .andExpect {
                status { isOk() }
                jsonPath("$.components.securitySchemes.bearerAuth.type") { value("http") }
                jsonPath("$.components.securitySchemes.bearerAuth.scheme") { value("bearer") }
            }
    }

    @Test
    fun `springdoc exposes public and admin groups`() {
        mockMvc.get("/v3/api-docs/public").andExpect { status { isOk() } }
        mockMvc.get("/v3/api-docs/admin").andExpect { status { isOk() } }
    }
}
