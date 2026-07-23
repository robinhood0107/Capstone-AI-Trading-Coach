package com.capstone.decision.infrastructure.openapi

import com.capstone.decision.application.principle.CatalogRuleDefinition
import com.capstone.decision.application.principle.PrincipleContract
import io.swagger.v3.oas.models.Components
import io.swagger.v3.oas.models.OpenAPI
import io.swagger.v3.oas.models.info.Info
import io.swagger.v3.oas.models.media.ArraySchema
import io.swagger.v3.oas.models.media.BooleanSchema
import io.swagger.v3.oas.models.media.IntegerSchema
import io.swagger.v3.oas.models.media.NumberSchema
import io.swagger.v3.oas.models.media.ObjectSchema
import io.swagger.v3.oas.models.media.Schema
import io.swagger.v3.oas.models.media.StringSchema
import io.swagger.v3.oas.models.security.SecurityRequirement
import io.swagger.v3.oas.models.security.SecurityScheme
import org.springdoc.core.customizers.OpenApiCustomizer
import org.springdoc.core.models.GroupedOpenApi
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.core.io.ClassPathResource
import java.math.BigDecimal
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

    @Bean
    fun principleContractSchemas(contract: PrincipleContract): OpenApiCustomizer =
        // annotation inference가 잃는 rule tuple과 exact 오류 envelope를 canonical catalog 값으로 다시 구성한다.
        OpenApiCustomizer { openApi ->
            openApi.components.addSchemas("PrincipleRule", principleRuleSchema(contract))
            openApi.components.addSchemas(
                "PrincipleValidationErrorResponse",
                errorEnvelope(
                    error =
                        errorSchema(
                            code = "VALIDATION_ERROR",
                            message = "Request validation failed.",
                            details =
                                objectSchema(
                                    properties =
                                        linkedMapOf(
                                            "violations" to
                                                ArraySchema()
                                                    .items(
                                                        objectSchema(
                                                            properties =
                                                                linkedMapOf(
                                                                    "field" to
                                                                        StringSchema()
                                                                            .minLength(1)
                                                                            .maxLength(512)
                                                                            .pattern(
                                                                                "^/(?:[^~/]|~0|~1)*(?:/(?:[^~/]|~0|~1)*)*$",
                                                                            ),
                                                                    "reason" to
                                                                        StringSchema()._enum(VALIDATION_REASONS),
                                                                ),
                                                            required = listOf("field", "reason"),
                                                        ),
                                                    ).minItems(1)
                                                    .maxItems(64),
                                        ),
                                    required = listOf("violations"),
                                ),
                        ),
                    exampleDetails =
                        mapOf(
                            "violations" to
                                listOf(
                                    mapOf(
                                        "field" to "/query/cursor",
                                        "reason" to "INVALID_CURSOR",
                                    ),
                                ),
                        ),
                ),
            )
            openApi.components.addSchemas(
                "PrincipleUnauthorizedErrorResponse",
                errorEnvelope(
                    error = errorSchema("UNAUTHORIZED", "Authentication is required.", emptyDetailsSchema()),
                    exampleDetails = emptyMap(),
                ),
            )
            openApi.components.addSchemas(
                "PrincipleForbiddenErrorResponse",
                errorEnvelope(
                    error = errorSchema("FORBIDDEN", "Access is denied.", emptyDetailsSchema()),
                    exampleDetails = emptyMap(),
                ),
            )
            openApi.components.addSchemas(
                "PrincipleNotFoundErrorResponse",
                errorEnvelope(
                    error = errorSchema("NOT_FOUND", "Resource was not found.", emptyDetailsSchema()),
                    exampleDetails = emptyMap(),
                ),
            )
            openApi.components.addSchemas(
                "PrincipleConflictErrorResponse",
                errorEnvelope(
                    error =
                        errorSchema(
                            code = "CONFLICT",
                            message = "Resource conflict.",
                            details =
                                objectSchema(
                                    properties =
                                        linkedMapOf(
                                            "expectedVersion" to boundedVersionSchema(),
                                            "currentVersion" to boundedVersionSchema(),
                                        ),
                                    required = listOf("expectedVersion", "currentVersion"),
                                ),
                        ),
                    exampleDetails = mapOf("expectedVersion" to 1, "currentVersion" to 2),
                ),
            )
            openApi.components.addSchemas(
                "PrincipleVersionExhaustedErrorResponse",
                errorEnvelope(
                    error =
                        errorSchema(
                            code = "VERSION_EXHAUSTED",
                            message = "Principle version limit was reached.",
                            details =
                                objectSchema(
                                    properties =
                                        linkedMapOf(
                                            "currentVersion" to IntegerSchema()._const(Int.MAX_VALUE),
                                        ),
                                    required = listOf("currentVersion"),
                                ),
                        ),
                    exampleDetails = mapOf("currentVersion" to Int.MAX_VALUE),
                ),
            )
            openApi.components.addSchemas(
                "PrinciplePayloadTooLargeErrorResponse",
                errorEnvelope(
                    error =
                        errorSchema(
                            code = "PAYLOAD_TOO_LARGE",
                            message = "Request payload exceeded the configured safety limit.",
                            details =
                                objectSchema(
                                    properties =
                                        linkedMapOf(
                                            "maxBytes" to IntegerSchema()._const(REQUEST_MAX_BYTES),
                                        ),
                                    required = listOf("maxBytes"),
                                ),
                        ),
                    exampleDetails = mapOf("maxBytes" to REQUEST_MAX_BYTES),
                ),
            )
        }

    private fun principleRuleSchema(contract: PrincipleContract): Schema<*> {
        val definitions = contract.ruleDefinitions.values.sortedBy(CatalogRuleDefinition::order)
        val schema =
            objectSchema(
                properties =
                    linkedMapOf(
                        "ruleId" to StringSchema()._enum(definitions.map(CatalogRuleDefinition::ruleId)),
                        "ruleType" to StringSchema()._enum(definitions.map(CatalogRuleDefinition::ruleType).distinct()),
                        "metric" to StringSchema()._enum(definitions.map(CatalogRuleDefinition::metric)),
                        "operator" to StringSchema()._enum(definitions.map(CatalogRuleDefinition::operator).distinct()),
                        "threshold" to Schema<Any>().types(linkedSetOf("number", "integer")),
                        "severity" to StringSchema()._enum(SEVERITIES),
                        "enabled" to BooleanSchema(),
                    ),
                required = RULE_FIELDS,
            )
        schema.oneOf = definitions.map(::ruleVariant)
        return schema
    }

    private fun ruleVariant(definition: CatalogRuleDefinition): Schema<*> {
        val threshold =
            when (definition.jsonType) {
                "integer" -> IntegerSchema()
                "number" ->
                    NumberSchema().also {
                        it.multipleOf = BigDecimal.ONE.movePointLeft(definition.maxNormalizedScale)
                    }
                else -> error("Unsupported Principle threshold JSON type: ${definition.jsonType}")
            }
        threshold.minimum = definition.minimum
        threshold.maximum = definition.maximum

        val disabledCondition =
            Schema<Any>().also {
                it.setIf(
                    objectSchema(
                        properties = linkedMapOf("enabled" to BooleanSchema()._const(false)),
                        required = listOf("enabled"),
                    ),
                )
                it.setThen(
                    objectSchema(
                        properties =
                            linkedMapOf(
                                "severity" to StringSchema()._const(definition.disabledSeverity),
                            ),
                    ),
                )
                it.setElse(
                    objectSchema(
                        properties =
                            linkedMapOf(
                                "severity" to
                                    StringSchema()._enum(
                                        SEVERITIES.filter(definition.enabledSeverities::contains),
                                    ),
                            ),
                    ),
                )
            }
        return ObjectSchema().also {
            it.properties =
                linkedMapOf(
                    "ruleId" to StringSchema()._const(definition.ruleId),
                    "ruleType" to StringSchema()._const(definition.ruleType),
                    "metric" to StringSchema()._const(definition.metric),
                    "operator" to StringSchema()._const(definition.operator),
                    "threshold" to threshold,
                )
            it.allOf = listOf(disabledCondition)
        }
    }

    private fun errorEnvelope(
        error: Schema<*>,
        exampleDetails: Map<String, Any>,
    ): Schema<*> =
        objectSchema(
            properties =
                linkedMapOf(
                    "success" to BooleanSchema()._const(false),
                    "requestId" to StringSchema().minLength(1).maxLength(128),
                    "data" to Schema<Any>().types(linkedSetOf("null")),
                    "warnings" to Schema<Any>().types(linkedSetOf("array"))._const(emptyList<Any>()),
                    "error" to error,
                ),
            required = listOf("success", "requestId", "data", "warnings", "error"),
        ).also { schema ->
            val code = requireNotNull(error.properties["code"]).getConst()
            val message = requireNotNull(error.properties["message"]).getConst()
            schema.example =
                linkedMapOf(
                    "success" to false,
                    "requestId" to "req_20260723_example",
                    "data" to null,
                    "warnings" to emptyList<Any>(),
                    "error" to
                        linkedMapOf(
                            "code" to code,
                            "message" to message,
                            "details" to exampleDetails,
                        ),
                )
        }

    private fun errorSchema(
        code: String,
        message: String,
        details: Schema<*>,
    ): Schema<*> =
        objectSchema(
            properties =
                linkedMapOf(
                    "code" to StringSchema()._const(code),
                    "message" to StringSchema()._const(message),
                    "details" to details,
                ),
            required = listOf("code", "message", "details"),
        )

    private fun emptyDetailsSchema(): Schema<*> =
        objectSchema(
            properties = linkedMapOf(),
            required = emptyList(),
        )._const(emptyMap<String, Any>())

    private fun boundedVersionSchema(): IntegerSchema =
        IntegerSchema().also {
            it.minimum = BigDecimal.ONE
            it.maximum = BigDecimal.valueOf(Int.MAX_VALUE.toLong())
        }

    private fun objectSchema(
        properties: LinkedHashMap<String, Schema<*>>,
        required: List<String> = emptyList(),
    ): ObjectSchema =
        ObjectSchema().also {
            it.properties = properties
            it.required = required
            it.additionalProperties = false
        }

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
        private const val REQUEST_MAX_BYTES = 1_048_576
        private val RULE_FIELDS =
            listOf("ruleId", "ruleType", "metric", "operator", "threshold", "severity", "enabled")
        private val SEVERITIES = listOf("ALLOW", "WARN", "BLOCK")
        private val VALIDATION_REASONS =
            listOf(
                "REQUIRED",
                "UNKNOWN_FIELD",
                "INVALID_FORMAT",
                "INVALID_ENUM",
                "UNAVAILABLE",
                "OUT_OF_RANGE",
                "INVALID_SCALE",
                "TOO_FEW_ITEMS",
                "TOO_MANY_ITEMS",
                "DUPLICATE",
                "INVALID_COMBINATION",
                "INVALID_CURSOR",
            )
    }
}
