package com.capstone.decision.infrastructure.openapi

import io.swagger.v3.oas.models.Components
import io.swagger.v3.oas.models.OpenAPI
import io.swagger.v3.oas.models.info.Info
import io.swagger.v3.oas.models.security.SecurityRequirement
import io.swagger.v3.oas.models.security.SecurityScheme
import org.springdoc.core.models.GroupedOpenApi
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.core.io.ClassPathResource
import java.security.MessageDigest
import java.util.HexFormat

// swagger-ui 수동 smoke와 이후 OpenAPI diff CI가 같은 bearer/group 정의를 보게 한다.
@Configuration
class OpenApiConfig {
    @Bean
    fun openApi(): OpenAPI =
        OpenAPI()
            .openapi("3.1.1")
            .jsonSchemaDialect(OAS_BASE_DIALECT)
            .info(
                Info()
                    .title("Decision Platform API")
                    .version("0.0.1"),
            ).extensions(
                mapOf(
                    S21_CONTRACT_ID_EXTENSION to S21_CONTRACT_ID,
                    S21_CONTRACT_DIGEST_EXTENSION to catalogDigest(),
                ),
            ).components(
                // Authorize 버튼이 JWT Bearer 토큰을 표준 방식으로 주입하도록 scheme을 명시한다.
                Components().addSecuritySchemes(
                    "bearerAuth",
                    SecurityScheme()
                        .type(SecurityScheme.Type.HTTP)
                        .scheme("bearer")
                        .bearerFormat("JWT")
                        .description("HS256 JWT. 서버는 configured issuer/audience와 DB actor 상태/version을 검증한다."),
                ),
            ).addSecurityItem(SecurityRequirement().addList("bearerAuth"))

    @Bean
    fun publicApi(): GroupedOpenApi =
        // 프론트가 사용하는 일반 API 문서를 admin 운영 API와 분리해 탐색성을 높인다.
        GroupedOpenApi
            .builder()
            .group("public")
            .pathsToMatch("/api/v1/**")
            .pathsToExclude("/api/v1/admin/**")
            .build()

    @Bean
    fun adminApi(): GroupedOpenApi =
        // ADMIN 전용/운영성 endpoint는 별도 그룹으로 권한 경계를 눈에 보이게 한다.
        GroupedOpenApi
            .builder()
            .group("admin")
            .pathsToMatch("/api/v1/admin/**", "/api/v1/async-jobs/**", "/api/v1/events/**", "/api/v1/test/admin")
            .build()

    private fun catalogDigest(): String {
        // build가 canonical catalog bytes를 classpath에 그대로 복사하므로 extension은 사람이 입력할 수 없다.
        val bytes = ClassPathResource(S21_CATALOG_RESOURCE).inputStream.use { it.readAllBytes() }
        check(bytes.isNotEmpty() && bytes.last() == '\n'.code.toByte()) {
            "S2.1 Principle catalog must be a non-empty LF-terminated resource."
        }
        return HexFormat
            .of()
            .formatHex(MessageDigest.getInstance("SHA-256").digest(bytes))
    }

    companion object {
        private const val OAS_BASE_DIALECT = "https://spec.openapis.org/oas/3.1/dialect/base"
        private const val S21_CATALOG_RESOURCE = "contracts/s2-1-principle-contract.v1.json"
        private const val S21_CONTRACT_ID_EXTENSION = "x-s2-1-contract-id"
        private const val S21_CONTRACT_DIGEST_EXTENSION = "x-s2-1-contract-sha256"
        private const val S21_CONTRACT_ID = "s2-1-principle-contract/v1"
    }
}
