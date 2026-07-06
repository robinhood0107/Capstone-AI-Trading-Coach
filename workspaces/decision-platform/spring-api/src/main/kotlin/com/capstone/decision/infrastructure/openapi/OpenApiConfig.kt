package com.capstone.decision.infrastructure.openapi

import io.swagger.v3.oas.models.Components
import io.swagger.v3.oas.models.OpenAPI
import io.swagger.v3.oas.models.security.SecurityRequirement
import io.swagger.v3.oas.models.security.SecurityScheme
import org.springdoc.core.models.GroupedOpenApi
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration

// swagger-ui 수동 smoke와 이후 OpenAPI diff CI가 같은 bearer/group 정의를 보게 한다.
@Configuration
class OpenApiConfig {
    @Bean
    fun openApi(): OpenAPI =
        OpenAPI()
            .components(
                // Authorize 버튼이 JWT Bearer 토큰을 표준 방식으로 주입하도록 scheme을 명시한다.
                Components().addSecuritySchemes(
                    "bearerAuth",
                    SecurityScheme()
                        .type(SecurityScheme.Type.HTTP)
                        .scheme("bearer")
                        .bearerFormat("JWT"),
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
}
