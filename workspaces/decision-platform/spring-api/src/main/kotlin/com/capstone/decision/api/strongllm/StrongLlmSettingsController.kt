package com.capstone.decision.api.strongllm

import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.application.strongllm.PutStrongLlmSettingsCommand
import com.capstone.decision.application.strongllm.StrongLlmSettingsService
import io.swagger.v3.oas.annotations.Operation
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.HttpStatus
import org.springframework.http.MediaType
import org.springframework.http.ResponseEntity
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import tools.jackson.core.JacksonException
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.JsonNode
import tools.jackson.databind.json.JsonMapper

/**
 * Strong LLM 설정을 쓰는 표면이다. **읽기는 여기 없다** — 현재 설정과 키의 마지막 네 글자는
 * `GET /api/v2/rag/corpus-status`가 이미 돌려주는 상태에 함께 실린다. 읽기 endpoint를 따로 두면
 * root OpenAPI에 operation이 하나 더 늘고, 그 사슬은 승인된 전이로만 움직인다.
 *
 * 응답 본문이 없다. 키를 담을 수 있는 응답을 아예 만들지 않는 것이 키를 응답에서 지우는 것보다
 * 확실하다.
 */
@RestController
@RequestMapping("/api/v2/strong-llm", produces = [MediaType.APPLICATION_JSON_VALUE])
class StrongLlmSettingsController(
    private val service: StrongLlmSettingsService,
) {
    private val parser = StrongLlmSettingsRequestParser()

    @Operation(operationId = "putStrongLlmSettings")
    @PutMapping("/settings", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun putSettings(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ResponseEntity<Void> {
        require(request.queryString == null)
        service.put(principal.userId, parser.parse(body.orEmpty()))
        return ResponseEntity.status(HttpStatus.NO_CONTENT).build()
    }
}

/** 알 수 없는 필드와 잘못된 값을 body 원문을 되비추지 않고 닫는다. */
internal class StrongLlmSettingsRequestParser {
    private val mapper =
        JsonMapper
            .builder(
                JsonFactory
                    .builder()
                    .streamReadConstraints(
                        StreamReadConstraints
                            .builder()
                            .maxNestingDepth(2)
                            .maxDocumentLength(MAX_BODY_BYTES.toLong())
                            .maxTokenCount(64)
                            .maxNumberLength(8)
                            .maxStringLength(MAX_KEY_CHARS)
                            .maxNameLength(64)
                            .build(),
                    ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                    .build(),
            ).build()

    fun parse(body: String): PutStrongLlmSettingsCommand {
        val root =
            try {
                mapper.readTree(body)
            } catch (_: JacksonException) {
                throw IllegalArgumentException("STRONG_LLM_SETTINGS_BODY_INVALID")
            }
        require(root != null && root.isObject)
        require(
            root
                .properties()
                .map { it.key }
                .toSet()
                .all { it in FIELDS },
        )
        val provider = enumField(root, "provider", PROVIDERS)
        val fallbackProvider = optionalEnumField(root, "fallbackProvider", PROVIDERS)
        val baseUrl = optionalPattern(root, "baseUrl", BASE_URL)
        val fallbackBaseUrl = optionalPattern(root, "fallbackBaseUrl", BASE_URL)
        require(provider != "custom" || baseUrl != null)
        require(fallbackProvider != "custom" || fallbackBaseUrl != null)
        val cap = root.get("dailyGenerateCallCap")
        require(cap != null && cap.isIntegralNumber && cap.asInt() in 1..500)
        return PutStrongLlmSettingsCommand(
            provider = provider,
            fallbackProvider = fallbackProvider,
            modelId = optionalPattern(root, "modelId", MODEL_ID),
            fallbackModelId = optionalPattern(root, "fallbackModelId", MODEL_ID),
            baseUrl = baseUrl,
            fallbackBaseUrl = fallbackBaseUrl,
            answerLanguage = enumField(root, "answerLanguage", LANGUAGES),
            dailyGenerateCallCap = cap.asInt(),
            apiKey = optionalKey(root, "apiKey"),
            fallbackApiKey = optionalKey(root, "fallbackApiKey"),
        )
    }

    private fun enumField(
        root: JsonNode,
        name: String,
        allowed: Set<String>,
    ): String {
        val node = root.get(name)
        require(node != null && node.isString && node.stringValue() in allowed)
        return node.stringValue()
    }

    private fun optionalEnumField(
        root: JsonNode,
        name: String,
        allowed: Set<String>,
    ): String? {
        val node = root.get(name) ?: return null
        if (node.isNull) return null
        require(node.isString && node.stringValue() in allowed)
        return node.stringValue()
    }

    private fun optionalPattern(
        root: JsonNode,
        name: String,
        pattern: Regex,
    ): String? {
        val node = root.get(name) ?: return null
        if (node.isNull) return null
        require(node.isString && pattern.matches(node.stringValue()))
        return node.stringValue()
    }

    /**
     * 키만 빈 문자열을 받는다. 빈 문자열은 "지운다", 없음은 "그대로 둔다"이며 그 둘을 합치면
     * 설정만 바꾸려는 요청이 저장된 키를 조용히 지운다.
     */
    private fun optionalKey(
        root: JsonNode,
        name: String,
    ): String? {
        val node = root.get(name) ?: return null
        if (node.isNull) return null
        require(node.isString)
        val value = node.stringValue()
        require(value.length <= MAX_KEY_CHARS)
        require(value.isEmpty() || (value.length >= 8 && KEY.matches(value)))
        return value
    }

    private companion object {
        const val MAX_BODY_BYTES = 16_384
        const val MAX_KEY_CHARS = 4_096
        val FIELDS =
            setOf(
                "provider",
                "fallbackProvider",
                "modelId",
                "fallbackModelId",
                "baseUrl",
                "fallbackBaseUrl",
                "answerLanguage",
                "dailyGenerateCallCap",
                "apiKey",
                "fallbackApiKey",
            )
        val PROVIDERS = setOf("vertex", "openai", "anthropic", "google_genai", "custom")
        val LANGUAGES = setOf("ko", "en")
        val MODEL_ID = Regex("^[a-z][a-z0-9._-]{2,127}$")
        val BASE_URL = Regex("^https://[A-Za-z0-9._~:/?#@!\$&()*+,;=%-]{3,256}\$")

        // provider 키는 대체로 ASCII 영숫자와 몇 개의 구분자다. 공백과 제어문자를 받지 않아
        // 실수로 붙여 넣은 줄바꿈이 그대로 저장되지 않게 한다.
        val KEY = Regex("^[A-Za-z0-9._:/+=-]{8,4096}$")
    }
}
