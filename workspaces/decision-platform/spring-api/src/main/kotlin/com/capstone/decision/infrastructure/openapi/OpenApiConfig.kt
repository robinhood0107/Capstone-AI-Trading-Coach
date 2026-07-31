package com.capstone.decision.infrastructure.openapi

import com.capstone.decision.api.rag.RagConsentResponse
import com.capstone.decision.api.rag.RagFeedbackResponse
import com.capstone.decision.api.rag.RagSourceListResponse
import com.capstone.decision.application.principle.CatalogRuleDefinition
import com.capstone.decision.application.principle.PrincipleContract
import io.swagger.v3.core.converter.ModelConverters
import io.swagger.v3.core.util.Json31
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
                    S23_CONTRACT_ID_EXTENSION to S23_CONTRACT_ID,
                    S23_CONTRACT_DIGEST_EXTENSION to resourceDigest(S23_CATALOG_RESOURCE),
                    S32_CONTRACT_ID_EXTENSION to S32_CONTRACT_ID,
                    S32_CONTRACT_DIGEST_EXTENSION to resourceDigest(S32_CATALOG_RESOURCE),
                    S33_CONTRACT_ID_EXTENSION to S33_CONTRACT_ID,
                    S33_CONTRACT_DIGEST_EXTENSION to resourceDigest(S33_CATALOG_RESOURCE),
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

    @Bean
    fun ragContractSchemas(): OpenApiCustomizer =
        // 실제 wire 응답은 공통 envelope이므로 data 객체와 성공 응답 schema를 분리해 고정한다.
        OpenApiCustomizer { openApi ->
            openApi.components.addSchemas(
                S44_RAG_ASK_REQUEST_COMPONENT,
                contractSchema(S44_RAG_ASK_REQUEST_RESOURCE, S44_RAG_ASK_REQUEST_COMPONENT),
            )
            openApi.components.addSchemas(
                S44_RAG_ANSWER_COMPONENT,
                contractSchema(S44_RAG_ANSWER_RESOURCE, S44_RAG_ANSWER_COMPONENT),
            )
            openApi.components.addSchemas(
                S44_RAG_HISTORY_PAGE_COMPONENT,
                contractSchema(S44_RAG_HISTORY_PAGE_RESOURCE, S44_RAG_HISTORY_PAGE_COMPONENT),
            )
            openApi.components.addSchemas(
                S44_RAG_HISTORY_DETAIL_COMPONENT,
                contractSchema(S44_RAG_HISTORY_DETAIL_RESOURCE, S44_RAG_HISTORY_DETAIL_COMPONENT),
            )
            openApi.components.addSchemas(
                S44_RAG_FEEDBACK_REQUEST_COMPONENT,
                contractSchema(S44_RAG_FEEDBACK_REQUEST_RESOURCE, S44_RAG_FEEDBACK_REQUEST_COMPONENT),
            )
            openApi.components.addSchemas(
                S44_RAG_CONSENT_REQUEST_COMPONENT,
                contractSchema(S44_RAG_CONSENT_REQUEST_RESOURCE, S44_RAG_CONSENT_REQUEST_COMPONENT),
            )
            openApi.components.addSchemas(
                S44_RAG_ANSWER_ENVELOPE_COMPONENT,
                successEnvelope(schemaRef(S44_RAG_ANSWER_COMPONENT)),
            )
            openApi.components.addSchemas(
                S44_RAG_HISTORY_PAGE_ENVELOPE_COMPONENT,
                successEnvelope(schemaRef(S44_RAG_HISTORY_PAGE_COMPONENT)),
            )
            openApi.components.addSchemas(
                S44_RAG_HISTORY_DETAIL_ENVELOPE_COMPONENT,
                successEnvelope(schemaRef(S44_RAG_HISTORY_DETAIL_COMPONENT)),
            )
            ModelConverters
                .getInstance()
                .readAll(RagFeedbackResponse::class.java)
                .plus(ModelConverters.getInstance().readAll(RagConsentResponse::class.java))
                .forEach(openApi.components::addSchemas)
            openApi.components.schemas.getValue(S44_RAG_FEEDBACK_RESPONSE_COMPONENT).also {
                it.types = linkedSetOf("object")
                it.required = listOf("answerId", "helpful")
                it.additionalProperties = false
            }
            openApi.components.schemas.getValue(S44_RAG_CONSENT_RESPONSE_COMPONENT).also {
                it.types = linkedSetOf("object")
                it.required =
                    listOf(
                        "consentEventId",
                        "consentType",
                        "action",
                        "policyVersion",
                        "createdAt",
                    )
                it.additionalProperties = false
            }
            openApi.components.addSchemas(
                S44_RAG_FEEDBACK_ENVELOPE_COMPONENT,
                successEnvelope(schemaRef(S44_RAG_FEEDBACK_RESPONSE_COMPONENT)),
            )
            openApi.components.addSchemas(
                S44_RAG_CONSENT_ENVELOPE_COMPONENT,
                successEnvelope(schemaRef(S44_RAG_CONSENT_RESPONSE_COMPONENT)),
            )
            // response annotation을 envelope로 교체해도 중첩 DTO component가 누락되지 않게 명시적으로 해석한다.
            ModelConverters
                .getInstance()
                .readAll(RagSourceListResponse::class.java)
                .forEach(openApi.components::addSchemas)
            openApi.components.schemas.getValue(S4_RAG_SOURCE_COMPONENT).also {
                // OAS 3.1 validator가 scalar payload에 object 전용 제약을 건너뛰지 않게 타입도 명시한다.
                it.types = linkedSetOf("object")
                it.required = S4_RAG_SOURCE_FIELDS
                it.additionalProperties = false
                // 등록 직후 아직 check 이력이 없는 active source도 exact 7-field projection에서 null을 유지한다.
                it.properties["lastCheckedAt"] =
                    Schema<Any>()
                        .types(linkedSetOf("string", "null"))
                        .format("date-time")
            }
            openApi.components.schemas.getValue(S4_RAG_SOURCE_LIST_COMPONENT).also {
                it.types = linkedSetOf("object")
                it.required = listOf("items")
                it.additionalProperties = false
                it.properties["items"] =
                    ArraySchema()
                        .items(schemaRef(S4_RAG_SOURCE_COMPONENT))
                        .maxItems(S4_RAG_SOURCE_MAX_ITEMS)
            }
            openApi.components.addSchemas(
                S4_RAG_SOURCE_LIST_ENVELOPE_COMPONENT,
                successEnvelope(schemaRef(S4_RAG_SOURCE_LIST_COMPONENT)),
            )
            openApi.components.addSchemas(
                S4_RAG_VALIDATION_ERROR_COMPONENT,
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
                                                                    "reason" to StringSchema()._const("UNKNOWN_FIELD"),
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
                                        "field" to "/query/sourceTier",
                                        "reason" to "UNKNOWN_FIELD",
                                    ),
                                ),
                        ),
                ),
            )
            openApi.components.addSchemas(
                S4_RAG_UNAUTHORIZED_ERROR_COMPONENT,
                errorEnvelope(
                    error =
                        errorSchema(
                            "UNAUTHORIZED",
                            "Authentication is required.",
                            emptyDetailsSchema(),
                        ),
                    exampleDetails = emptyMap(),
                ),
            )
            openApi.components.addSchemas(
                S4_RAG_UNAVAILABLE_ERROR_COMPONENT,
                errorEnvelope(
                    error =
                        errorSchema(
                            "RAG_UNAVAILABLE",
                            "RAG source registry is unavailable.",
                            emptyDetailsSchema(),
                        ),
                    exampleDetails = emptyMap(),
                ),
            )
        }

    @Bean
    fun decisionContractSchemas(): OpenApiCustomizer =
        // strict String parser의 annotation inference를 generator-locked JSON Schema component로 교체한다.
        OpenApiCustomizer { openApi ->
            openApi.components.addSchemas(
                S23_REQUEST_COMPONENT,
                contractSchema(S23_REQUEST_SCHEMA_RESOURCE, S23_REQUEST_COMPONENT),
            )
            openApi.components.addSchemas(
                S23_DECISION_COMPONENT,
                contractSchema(S23_DECISION_SCHEMA_RESOURCE, S23_DECISION_COMPONENT),
            )
            openApi.components.addSchemas(
                S23_DECISION_ENVELOPE_COMPONENT,
                successEnvelope(schemaRef(S23_DECISION_COMPONENT)),
            )
            openApi.components.addSchemas(
                S23_AUDIT_COMPONENT,
                decisionAuditSchema(),
            )
            openApi.components.addSchemas(
                S23_AUDIT_ENVELOPE_COMPONENT,
                successEnvelope(schemaRef(S23_AUDIT_COMPONENT)),
            )
        }

    @Bean
    fun riskContractSchemas(): OpenApiCustomizer =
        // S2.4 nullable/source-missing 및 sanitized 3-field projection을 canonical JSON Schema로 고정한다.
        OpenApiCustomizer { openApi ->
            openApi.components.addSchemas(
                S24_KILL_SWITCH_REQUEST_COMPONENT,
                contractSchema(S24_KILL_SWITCH_REQUEST_RESOURCE, S24_KILL_SWITCH_REQUEST_COMPONENT),
            )
            openApi.components.addSchemas(
                S24_KILL_SWITCH_STATE_COMPONENT,
                contractSchema(S24_KILL_SWITCH_STATE_RESOURCE, S24_KILL_SWITCH_STATE_COMPONENT),
            )
            openApi.components.addSchemas(
                S24_PORTFOLIO_RISK_COMPONENT,
                contractSchema(S24_PORTFOLIO_RISK_RESOURCE, S24_PORTFOLIO_RISK_COMPONENT),
            )
            openApi.components.addSchemas(
                S24_KILL_SWITCH_ENVELOPE_COMPONENT,
                successEnvelope(schemaRef(S24_KILL_SWITCH_STATE_COMPONENT)),
            )
            openApi.components.addSchemas(
                S24_PORTFOLIO_RISK_ENVELOPE_COMPONENT,
                successEnvelope(schemaRef(S24_PORTFOLIO_RISK_COMPONENT)),
            )
            openApi.components.addSchemas(
                S24_RISK_ERROR_COMPONENT,
                riskErrorEnvelope(),
            )
        }

    @Bean
    fun brokerageContractSchemas(): OpenApiCustomizer =
        // S3.1 Brokerage Mock 요청/응답도 canonical schema resource를 그대로 component로 노출한다.
        OpenApiCustomizer { openApi ->
            openApi.components.addSchemas(
                S31_MOCK_ORDER_REQUEST_COMPONENT,
                contractSchema(S31_MOCK_ORDER_REQUEST_RESOURCE, S31_MOCK_ORDER_REQUEST_COMPONENT),
            )
            openApi.components.addSchemas(
                S31_MOCK_ORDER_RESPONSE_COMPONENT,
                contractSchema(S31_MOCK_ORDER_RESPONSE_RESOURCE, S31_MOCK_ORDER_RESPONSE_COMPONENT),
            )
            openApi.components.addSchemas(
                S31_MOCK_ORDER_DETAIL_COMPONENT,
                contractSchema(S31_MOCK_ORDER_DETAIL_RESOURCE, S31_MOCK_ORDER_DETAIL_COMPONENT),
            )
            openApi.components.addSchemas(
                S31_MOCK_BALANCE_COMPONENT,
                contractSchema(S31_MOCK_BALANCE_RESOURCE, S31_MOCK_BALANCE_COMPONENT),
            )
            openApi.components.addSchemas(
                S31_MOCK_BUYABLE_COMPONENT,
                contractSchema(S31_MOCK_BUYABLE_RESOURCE, S31_MOCK_BUYABLE_COMPONENT),
            )
            openApi.components.addSchemas(
                S31_MOCK_ORDER_ENVELOPE_COMPONENT,
                successEnvelope(schemaRef(S31_MOCK_ORDER_RESPONSE_COMPONENT)),
            )
            openApi.components.addSchemas(
                S31_MOCK_ORDER_DETAIL_ENVELOPE_COMPONENT,
                successEnvelope(schemaRef(S31_MOCK_ORDER_DETAIL_COMPONENT)),
            )
            openApi.components.addSchemas(
                S31_MOCK_BALANCE_ENVELOPE_COMPONENT,
                successEnvelope(schemaRef(S31_MOCK_BALANCE_COMPONENT)),
            )
            openApi.components.addSchemas(
                S31_MOCK_BUYABLE_ENVELOPE_COMPONENT,
                successEnvelope(schemaRef(S31_MOCK_BUYABLE_COMPONENT)),
            )
            openApi.components.addSchemas(
                S32_PAPER_ORDER_REQUEST_COMPONENT,
                contractSchema(S32_PAPER_ORDER_REQUEST_RESOURCE, S32_PAPER_ORDER_REQUEST_COMPONENT),
            )
            openApi.components.addSchemas(
                S32_PAPER_ORDER_RESPONSE_COMPONENT,
                contractSchema(S32_PAPER_ORDER_RESPONSE_RESOURCE, S32_PAPER_ORDER_RESPONSE_COMPONENT),
            )
            openApi.components.addSchemas(
                S32_ORDER_DETAIL_COMPONENT,
                contractSchema(S32_ORDER_DETAIL_RESOURCE, S32_ORDER_DETAIL_COMPONENT),
            )
            openApi.components.addSchemas(
                S32_PAPER_BALANCE_COMPONENT,
                contractSchema(S32_PAPER_BALANCE_RESOURCE, S32_PAPER_BALANCE_COMPONENT),
            )
            openApi.components.addSchemas(
                S32_PAPER_BUYABLE_COMPONENT,
                contractSchema(S32_PAPER_BUYABLE_RESOURCE, S32_PAPER_BUYABLE_COMPONENT),
            )
            openApi.components.addSchemas(
                S32_PAPER_ORDER_ENVELOPE_COMPONENT,
                successEnvelope(schemaRef(S32_PAPER_ORDER_RESPONSE_COMPONENT)),
            )
            openApi.components.addSchemas(
                S32_ORDER_DETAIL_ENVELOPE_COMPONENT,
                successEnvelope(schemaRef(S32_ORDER_DETAIL_COMPONENT)),
            )
            openApi.components.addSchemas(
                S32_PAPER_BALANCE_ENVELOPE_COMPONENT,
                successEnvelope(schemaRef(S32_PAPER_BALANCE_COMPONENT)),
            )
            openApi.components.addSchemas(
                S32_PAPER_BUYABLE_ENVELOPE_COMPONENT,
                successEnvelope(schemaRef(S32_PAPER_BUYABLE_COMPONENT)),
            )
            // 관측 schema는 offline writer 계약만 설명하며 public 체결 보고 route를 만들지 않는다.
            openApi.components.addSchemas(
                S33_FILL_OBSERVATION_COMPONENT,
                contractSchema(S33_FILL_OBSERVATION_RESOURCE, S33_FILL_OBSERVATION_COMPONENT),
            )
            openApi.components.addSchemas(
                S33_RECONCILE_COMPONENT,
                contractSchema(S33_RECONCILE_RESOURCE, S33_RECONCILE_COMPONENT),
            )
            openApi.components.addSchemas(
                S33_FILL_PAGE_COMPONENT,
                contractSchema(S33_FILL_PAGE_RESOURCE, S33_FILL_PAGE_COMPONENT),
            )
            openApi.components.addSchemas(
                S33_RECONCILE_ENVELOPE_COMPONENT,
                successEnvelope(schemaRef(S33_RECONCILE_COMPONENT)),
            )
            openApi.components.addSchemas(
                S33_FILL_PAGE_ENVELOPE_COMPONENT,
                successEnvelope(schemaRef(S33_FILL_PAGE_COMPONENT)),
            )
        }

    private fun riskErrorEnvelope(): Schema<*> =
        objectSchema(
            properties =
                linkedMapOf(
                    "success" to BooleanSchema()._const(false),
                    "requestId" to StringSchema().minLength(1).maxLength(128),
                    "data" to Schema<Any>().types(linkedSetOf("null")),
                    "warnings" to Schema<Any>().types(linkedSetOf("array"))._const(emptyList<Any>()),
                    "error" to
                        objectSchema(
                            properties =
                                linkedMapOf(
                                    "code" to StringSchema()._enum(S24_RISK_ERROR_CODES),
                                    "message" to StringSchema().minLength(1).maxLength(512),
                                    "details" to ObjectSchema().additionalProperties(true),
                                ),
                            required = listOf("code", "message", "details"),
                        ),
                ),
            required = listOf("success", "requestId", "data", "warnings", "error"),
        )

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
                        "evidenceRequirement" to
                            StringSchema()._enum(
                                contract.evidenceRequirements.map(Enum<*>::name).sorted(),
                            ),
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
                    "evidenceRequirement" to
                        StringSchema()._enum(
                            definition.evidenceRequirements.map(Enum<*>::name).sorted(),
                        ),
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

    private fun catalogDigest(): String = resourceDigest(S21_CATALOG_RESOURCE)

    private fun resourceDigest(resource: String): String {
        // build가 canonical catalog bytes를 classpath에 그대로 복사하므로 extension은 사람이 입력할 수 없다.
        val bytes = ClassPathResource(resource).inputStream.use { it.readAllBytes() }
        check(bytes.isNotEmpty() && bytes.last() == '\n'.code.toByte()) {
            "Canonical contract resource must be non-empty and LF-terminated."
        }
        return HexFormat
            .of()
            .formatHex(MessageDigest.getInstance("SHA-256").digest(bytes))
    }

    private fun contractSchema(
        resource: String,
        component: String,
    ): Schema<*> {
        val bytes = ClassPathResource(resource).inputStream.use { it.readAllBytes() }
        check(bytes.isNotEmpty() && bytes.last() == '\n'.code.toByte()) {
            "S2.3 OpenAPI schema resource must be non-empty and LF-terminated."
        }
        val standaloneRefs = "\"#/\$defs/"
        val componentRefs = "\"#/components/schemas/$component/\$defs/"
        val embedded = bytes.toString(Charsets.UTF_8).replace(standaloneRefs, componentRefs)
        return Json31.mapper().readValue(embedded, Schema::class.java)
    }

    private fun successEnvelope(dataSchema: Schema<*>): Schema<*> =
        objectSchema(
            properties =
                linkedMapOf(
                    "success" to BooleanSchema()._const(true),
                    "requestId" to StringSchema().minLength(1).maxLength(128),
                    "data" to dataSchema,
                    "warnings" to ArraySchema().items(schemaRef("ApiWarning")),
                    "error" to Schema<Any>().types(linkedSetOf("null")),
                ),
            required = listOf("success", "requestId", "data", "warnings", "error"),
        )

    private fun decisionAuditSchema(): Schema<*> =
        objectSchema(
            properties =
                linkedMapOf(
                    "auditId" to StringSchema().pattern("^aud_[0-9a-f]{32}$"),
                    "action" to StringSchema()._const("DECISION_EVALUATED"),
                    "requestId" to StringSchema().minLength(1).maxLength(128),
                    "createdAt" to StringSchema().format("date-time"),
                    "payload" to
                        objectSchema(
                            properties =
                                linkedMapOf(
                                    "evaluationId" to StringSchema().pattern("^evl_[0-9a-f]{32}$"),
                                    "decisionId" to StringSchema().pattern("^dec_[0-9a-f]{32}$"),
                                    "outcome" to StringSchema()._enum(listOf("ALLOW", "WARN", "HOLD", "BLOCK")),
                                    "principleVersionId" to StringSchema().pattern("^pvr_[0-9a-f]{32}$"),
                                    "semanticInputHash" to StringSchema().pattern("^[0-9a-f]{64}$"),
                                    "snapshotArtifactHash" to StringSchema().pattern("^[0-9a-f]{64}$"),
                                ),
                            required =
                                listOf(
                                    "evaluationId",
                                    "decisionId",
                                    "outcome",
                                    "principleVersionId",
                                    "semanticInputHash",
                                    "snapshotArtifactHash",
                                ),
                        ),
                ),
            required = listOf("auditId", "action", "requestId", "createdAt", "payload"),
        )

    private fun schemaRef(component: String): Schema<Any> = Schema<Any>().also { it.`$ref` = "#/components/schemas/$component" }

    companion object {
        private const val OAS_BASE_DIALECT = "https://spec.openapis.org/oas/3.1/dialect/base"
        private const val S21_CATALOG_RESOURCE = "contracts/s2-1-principle-contract.v1.json"
        private const val S21_CONTRACT_ID_EXTENSION = "x-s2-1-contract-id"
        private const val S21_CONTRACT_DIGEST_EXTENSION = "x-s2-1-contract-sha256"
        private const val S21_CONTRACT_ID = "s2-1-principle-contract/v1"
        private const val S4_RAG_SOURCE_COMPONENT = "RagSourceResponse"
        private const val S4_RAG_SOURCE_LIST_COMPONENT = "RagSourceListResponse"
        private const val S4_RAG_SOURCE_LIST_ENVELOPE_COMPONENT = "S4RagSourceListSuccessResponse"
        private const val S4_RAG_VALIDATION_ERROR_COMPONENT = "S4RagValidationErrorResponse"
        private const val S4_RAG_UNAUTHORIZED_ERROR_COMPONENT = "S4RagUnauthorizedErrorResponse"
        private const val S4_RAG_UNAVAILABLE_ERROR_COMPONENT = "S4RagUnavailableErrorResponse"
        private const val S4_RAG_SOURCE_MAX_ITEMS = 30
        private const val S44_RAG_ASK_REQUEST_RESOURCE = "contracts/s4-rag-ask-request.schema.json"
        private const val S44_RAG_ANSWER_RESOURCE = "contracts/s4-rag-answer.schema.json"
        private const val S44_RAG_HISTORY_PAGE_RESOURCE = "contracts/s4-rag-history-page.schema.json"
        private const val S44_RAG_HISTORY_DETAIL_RESOURCE = "contracts/s4-rag-history-detail.schema.json"
        private const val S44_RAG_FEEDBACK_REQUEST_RESOURCE = "contracts/s4-rag-feedback-request.schema.json"
        private const val S44_RAG_CONSENT_REQUEST_RESOURCE = "contracts/s4-rag-consent-request.schema.json"
        private const val S44_RAG_ASK_REQUEST_COMPONENT = "S44RagAskRequest"
        private const val S44_RAG_ANSWER_COMPONENT = "S44RagAnswer"
        private const val S44_RAG_HISTORY_PAGE_COMPONENT = "S44RagHistoryPage"
        private const val S44_RAG_HISTORY_DETAIL_COMPONENT = "S44RagHistoryDetail"
        private const val S44_RAG_FEEDBACK_REQUEST_COMPONENT = "S44RagFeedbackRequest"
        private const val S44_RAG_CONSENT_REQUEST_COMPONENT = "S44RagConsentRequest"
        private const val S44_RAG_ANSWER_ENVELOPE_COMPONENT = "S44RagAnswerSuccessResponse"
        private const val S44_RAG_HISTORY_PAGE_ENVELOPE_COMPONENT = "S44RagHistoryPageSuccessResponse"
        private const val S44_RAG_HISTORY_DETAIL_ENVELOPE_COMPONENT = "S44RagHistoryDetailSuccessResponse"
        private const val S44_RAG_FEEDBACK_RESPONSE_COMPONENT = "RagFeedbackResponse"
        private const val S44_RAG_CONSENT_RESPONSE_COMPONENT = "RagConsentResponse"
        private const val S44_RAG_FEEDBACK_ENVELOPE_COMPONENT = "S44RagFeedbackSuccessResponse"
        private const val S44_RAG_CONSENT_ENVELOPE_COMPONENT = "S44RagConsentSuccessResponse"
        private val S4_RAG_SOURCE_FIELDS =
            listOf(
                "sourceId",
                "title",
                "institution",
                "topic",
                "attribution",
                "canonicalUrl",
                "lastCheckedAt",
            )
        private const val S23_CATALOG_RESOURCE = "contracts/s2-3-decision-contract.v1.json"
        private const val S23_REQUEST_SCHEMA_RESOURCE = "contracts/s2-3-evaluate-order-request.schema.json"
        private const val S23_DECISION_SCHEMA_RESOURCE = "contracts/s2-3-decision-response.schema.json"
        private const val S23_CONTRACT_ID_EXTENSION = "x-s2-3-contract-id"
        private const val S23_CONTRACT_DIGEST_EXTENSION = "x-s2-3-contract-sha256"
        private const val S23_CONTRACT_ID = "s2-3-decision-contract/v1"
        private const val S23_REQUEST_COMPONENT = "S23EvaluateOrderRequest"
        private const val S23_DECISION_COMPONENT = "S23Decision"
        private const val S23_DECISION_ENVELOPE_COMPONENT = "S23DecisionSuccessResponse"
        private const val S23_AUDIT_COMPONENT = "S23DecisionAudit"
        private const val S23_AUDIT_ENVELOPE_COMPONENT = "S23DecisionAuditSuccessResponse"
        private const val S24_KILL_SWITCH_REQUEST_RESOURCE = "contracts/s2-4-kill-switch-request.schema.json"
        private const val S24_KILL_SWITCH_STATE_RESOURCE = "contracts/s2-4-kill-switch-state.schema.json"
        private const val S24_PORTFOLIO_RISK_RESOURCE = "contracts/s2-4-risk-portfolio.schema.json"
        private const val S24_KILL_SWITCH_REQUEST_COMPONENT = "S24KillSwitchRequest"
        private const val S24_KILL_SWITCH_STATE_COMPONENT = "S24KillSwitchState"
        private const val S24_PORTFOLIO_RISK_COMPONENT = "S24PortfolioRisk"
        private const val S24_KILL_SWITCH_ENVELOPE_COMPONENT = "S24KillSwitchSuccessResponse"
        private const val S24_PORTFOLIO_RISK_ENVELOPE_COMPONENT = "S24PortfolioRiskSuccessResponse"
        private const val S24_RISK_ERROR_COMPONENT = "S24RiskErrorResponse"
        private const val S31_MOCK_ORDER_REQUEST_RESOURCE = "contracts/s3-1-mock-order-request.schema.json"
        private const val S31_MOCK_ORDER_RESPONSE_RESOURCE = "contracts/s3-1-mock-order-response.schema.json"
        private const val S31_MOCK_ORDER_DETAIL_RESOURCE = "contracts/s3-1-mock-order-detail.schema.json"
        private const val S31_MOCK_BALANCE_RESOURCE = "contracts/s3-1-mock-balance.schema.json"
        private const val S31_MOCK_BUYABLE_RESOURCE = "contracts/s3-1-mock-buyable.schema.json"
        private const val S31_MOCK_ORDER_REQUEST_COMPONENT = "S31MockOrderRequest"
        private const val S31_MOCK_ORDER_RESPONSE_COMPONENT = "S31MockOrderSubmitted"
        private const val S31_MOCK_ORDER_DETAIL_COMPONENT = "S31MockOrderDetail"
        private const val S31_MOCK_BALANCE_COMPONENT = "S31MockBalance"
        private const val S31_MOCK_BUYABLE_COMPONENT = "S31MockBuyable"
        private const val S31_MOCK_ORDER_ENVELOPE_COMPONENT = "S31MockOrderSuccessResponse"
        private const val S31_MOCK_ORDER_DETAIL_ENVELOPE_COMPONENT = "S31MockOrderDetailSuccessResponse"
        private const val S31_MOCK_BALANCE_ENVELOPE_COMPONENT = "S31MockBalanceSuccessResponse"
        private const val S31_MOCK_BUYABLE_ENVELOPE_COMPONENT = "S31MockBuyableSuccessResponse"
        private const val S32_CATALOG_RESOURCE = "contracts/s3-2-internal-paper-contract.v1.json"
        private const val S32_PAPER_ORDER_REQUEST_RESOURCE = "contracts/s3-2-paper-order-request.schema.json"
        private const val S32_PAPER_ORDER_RESPONSE_RESOURCE = "contracts/s3-2-paper-order-response.schema.json"
        private const val S32_ORDER_DETAIL_RESOURCE = "contracts/s3-2-order-detail.schema.json"
        private const val S32_PAPER_BALANCE_RESOURCE = "contracts/s3-2-paper-balance.schema.json"
        private const val S32_PAPER_BUYABLE_RESOURCE = "contracts/s3-2-paper-buyable.schema.json"
        private const val S32_CONTRACT_ID_EXTENSION = "x-s3-2-contract-id"
        private const val S32_CONTRACT_DIGEST_EXTENSION = "x-s3-2-contract-sha256"
        private const val S32_CONTRACT_ID = "s3-2-internal-paper-contract/v1"
        private const val S32_PAPER_ORDER_REQUEST_COMPONENT = "S32PaperOrderRequest"
        private const val S32_PAPER_ORDER_RESPONSE_COMPONENT = "S32PaperOrder"
        private const val S32_ORDER_DETAIL_COMPONENT = "S32OrderDetail"
        private const val S32_PAPER_BALANCE_COMPONENT = "S32PaperBalance"
        private const val S32_PAPER_BUYABLE_COMPONENT = "S32PaperBuyable"
        private const val S32_PAPER_ORDER_ENVELOPE_COMPONENT = "S32PaperOrderSuccessResponse"
        private const val S32_ORDER_DETAIL_ENVELOPE_COMPONENT = "S32OrderDetailSuccessResponse"
        private const val S32_PAPER_BALANCE_ENVELOPE_COMPONENT = "S32PaperBalanceSuccessResponse"
        private const val S32_PAPER_BUYABLE_ENVELOPE_COMPONENT = "S32PaperBuyableSuccessResponse"
        private const val S33_CATALOG_RESOURCE = "contracts/s3-3-fill-contract.v1.json"
        private const val S33_FILL_OBSERVATION_RESOURCE = "contracts/s3-3-fill-observation.schema.json"
        private const val S33_RECONCILE_RESOURCE = "contracts/s3-3-reconcile-response.schema.json"
        private const val S33_FILL_PAGE_RESOURCE = "contracts/s3-3-fill-page.schema.json"
        private const val S33_CONTRACT_ID_EXTENSION = "x-s3-3-contract-id"
        private const val S33_CONTRACT_DIGEST_EXTENSION = "x-s3-3-contract-sha256"
        private const val S33_CONTRACT_ID = "s3-3-fill-contract/v1"
        private const val S33_FILL_OBSERVATION_COMPONENT = "S33FillObservation"
        private const val S33_RECONCILE_COMPONENT = "S33Reconcile"
        private const val S33_FILL_PAGE_COMPONENT = "S33FillPage"
        private const val S33_RECONCILE_ENVELOPE_COMPONENT = "S33ReconcileSuccessResponse"
        private const val S33_FILL_PAGE_ENVELOPE_COMPONENT = "S33FillPageSuccessResponse"
        private val S24_RISK_ERROR_CODES =
            listOf(
                "VALIDATION_ERROR",
                "UNAUTHORIZED",
                "FORBIDDEN",
                "CONFLICT",
                "RISK_UNAVAILABLE",
            )
        private const val REQUEST_MAX_BYTES = 1_048_576
        private val RULE_FIELDS =
            listOf(
                "ruleId",
                "ruleType",
                "metric",
                "operator",
                "threshold",
                "severity",
                "enabled",
                "evidenceRequirement",
            )
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
