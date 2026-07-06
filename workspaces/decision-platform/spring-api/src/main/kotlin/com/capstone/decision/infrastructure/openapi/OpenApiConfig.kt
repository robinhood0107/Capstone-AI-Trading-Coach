package com.capstone.decision.infrastructure.openapi

import io.swagger.v3.oas.models.Components
import io.swagger.v3.oas.models.OpenAPI
import io.swagger.v3.oas.models.security.SecurityRequirement
import io.swagger.v3.oas.models.security.SecurityScheme
import org.springdoc.core.models.GroupedOpenApi
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration

@Configuration
class OpenApiConfig {
    @Bean
    fun openApi(): OpenAPI =
        OpenAPI()
            .components(
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
        GroupedOpenApi
            .builder()
            .group("public")
            .pathsToMatch("/api/v1/**")
            .pathsToExclude("/api/v1/admin/**")
            .build()

    @Bean
    fun adminApi(): GroupedOpenApi =
        GroupedOpenApi
            .builder()
            .group("admin")
            .pathsToMatch("/api/v1/admin/**", "/api/v1/async-jobs/**", "/api/v1/events/**", "/api/v1/test/admin")
            .build()
}
