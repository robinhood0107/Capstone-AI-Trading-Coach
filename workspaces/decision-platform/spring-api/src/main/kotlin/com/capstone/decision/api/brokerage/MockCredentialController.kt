package com.capstone.decision.api.brokerage

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.infrastructure.brokerage.MockCredentialSettingsService
import com.capstone.decision.infrastructure.brokerage.MockCredentialSummary
import io.swagger.v3.oas.annotations.Operation
import jakarta.servlet.http.HttpServletRequest
import org.springframework.context.annotation.Profile
import org.springframework.http.CacheControl
import org.springframework.http.MediaType
import org.springframework.http.ResponseEntity
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import tools.jackson.core.JacksonException
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.json.JsonMapper
import java.nio.charset.StandardCharsets

/** Only the verified JWT owner can store three KIS_MOCK values; no caller-selected owner or mode exists. */
@RestController
@Profile("mars-full")
@RequestMapping("/api/v1/brokerage/mock/credential", produces = [MediaType.APPLICATION_JSON_VALUE])
class MockCredentialController(
    private val settings: MockCredentialSettingsService,
) {
    private val parser = MockCredentialRequestParser()

    @Operation(operationId = "putMockBrokerCredential")
    @PutMapping(consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun put(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ResponseEntity<Void> {
        if (request.queryString != null) throw ApiException(ErrorCode.VALIDATION_ERROR)
        val input = parser.parse(body.orEmpty())
        settings.save(principal.userId, input.appKey, input.appSecret, input.accountNo)
        return ResponseEntity.noContent().cacheControl(CacheControl.noStore()).build()
    }

    @Operation(operationId = "readMockBrokerCredentialSummary")
    @GetMapping
    fun summary(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<MockCredentialReadResponse> {
        if (request.queryString != null) throw ApiException(ErrorCode.VALIDATION_ERROR)
        val credential = settings.summary(principal.userId)
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            MockCredentialReadResponse(registered = credential != null, credential = credential),
        )
    }
}

data class MockCredentialReadResponse(
    val registered: Boolean,
    val credential: MockCredentialSummary?,
)

internal class MockCredentialInput(
    val appKey: String,
    val appSecret: String,
    val accountNo: String,
) {
    override fun toString(): String = "MockCredentialInput(<redacted>)"
}

/** Exact three-field parser; rejected JSON is never echoed or logged. */
internal class MockCredentialRequestParser {
    private val mapper =
        JsonMapper
            .builder(
                JsonFactory
                    .builder()
                    .streamReadConstraints(
                        StreamReadConstraints
                            .builder()
                            .maxDocumentLength(1_200)
                            .maxNestingDepth(2)
                            .maxTokenCount(16)
                            .maxStringLength(512)
                            .build(),
                    ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                    .build(),
            ).build()

    fun parse(body: String): MockCredentialInput {
        if (body.toByteArray(StandardCharsets.UTF_8).size > 1_200) throw ApiException(ErrorCode.VALIDATION_ERROR)
        val root =
            try {
                mapper.readTree(body)
            } catch (_: JacksonException) {
                throw ApiException(ErrorCode.VALIDATION_ERROR)
            }
        if (root == null || !root.isObject || root.properties().map { it.key }.toSet() != FIELDS) {
            throw ApiException(ErrorCode.VALIDATION_ERROR)
        }
        val appKey = root.get("appKey")?.takeIf { it.isString }?.stringValue() ?: throw ApiException(ErrorCode.VALIDATION_ERROR)
        val appSecret = root.get("appSecret")?.takeIf { it.isString }?.stringValue() ?: throw ApiException(ErrorCode.VALIDATION_ERROR)
        val accountNo = root.get("accountNo")?.takeIf { it.isString }?.stringValue() ?: throw ApiException(ErrorCode.VALIDATION_ERROR)
        if (
            appKey.length !in 8..256 ||
            !APP_KEY.matches(appKey) ||
            !APP_KEY_LAST4.matches(appKey.takeLast(4)) ||
            appSecret.length !in 8..512 ||
            appSecret.any { it.code !in 33..126 } ||
            !ACCOUNT_NO.matches(accountNo)
        ) {
            throw ApiException(ErrorCode.VALIDATION_ERROR)
        }
        return MockCredentialInput(appKey, appSecret, accountNo)
    }

    private companion object {
        val FIELDS = setOf("appKey", "appSecret", "accountNo")
        val APP_KEY = Regex("^[A-Za-z0-9._-]+$")
        val APP_KEY_LAST4 = Regex("^[A-Za-z0-9_-]{4}$")
        val ACCOUNT_NO = Regex("^[0-9]{10}$")
    }
}
