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

// swagger-ui 수동 smoke 전에 OpenAPI security/group 계약이 자동으로 노출되는지 확인한다.
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
        // 문서 endpoint는 permitAll이지만 실제 보안 체인 안에서 접근 가능해야 한다.
        mockMvc =
            MockMvcBuilders
                .webAppContextSetup(webApplicationContext)
                .apply<DefaultMockMvcBuilder>(springSecurity())
                .build()
    }

    // bearerAuth scheme이 없으면 swagger Authorize smoke가 토큰을 넣을 수 없다.
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

    // public/admin 그룹은 이후 API가 늘어날 때 문서 경계를 유지하는 기준점이다.
    @Test
    fun `springdoc exposes public and admin groups`() {
        mockMvc.get("/v3/api-docs/public").andExpect { status { isOk() } }
        mockMvc.get("/v3/api-docs/admin").andExpect { status { isOk() } }
    }
}
