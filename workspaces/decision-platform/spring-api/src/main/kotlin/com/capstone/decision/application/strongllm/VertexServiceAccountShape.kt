package com.capstone.decision.application.strongllm

import tools.jackson.core.JacksonException
import tools.jackson.databind.json.JsonMapper
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.Base64
import java.util.HexFormat

/**
 * 사용자가 등록하는 Vertex 자격증명의 모양이다: 서비스 계정 JSON 을 표준 Base64 로 감싼 한 줄.
 *
 * 운영자 비밀(`MARS_VERTEX_SERVICE_ACCOUNT_JSON_B64`)과 같은 모양을 쓰므로 Python 쪽 검증
 * (`VertexProviderSettings`)을 그대로 통과한다. API 키·ADC 는 받지 않는다 - 에이전트가 API 키 경로를
 * 일부러 막아 두었고, 서비스 계정이어야 사용자 자기 GCP 프로젝트로 과금이 간다.
 *
 * 값은 로그·예외 메시지·응답에 싣지 않는다. 판정은 참/거짓만 돌려준다.
 */
object VertexServiceAccountShape {
    private const val MAX_ENCODED_CHARS = 4_096
    private const val TOKEN_URI = "https://oauth2.googleapis.com/token"
    private val mapper = JsonMapper.builder().build()
    private val KEY_ID = Regex("^[0-9a-f]{4,}$")

    fun isValid(encoded: String): Boolean = parse(encoded) != null

    /**
     * 화면이 "저장됨 (…xxxx)" 에 쓸 네 글자. Base64 끝자리는 `=`·`+`·`/` 라 저장 경계의 모양 검사에
     * 걸리므로 서비스 계정 키 ID 끝 네 글자를 쓴다. 키 ID 가 없으면 원문 digest 의 끝 네 글자다.
     */
    fun displayLast4(encoded: String): String {
        val keyId = parse(encoded)?.privateKeyId?.lowercase()
        if (keyId != null && KEY_ID.matches(keyId)) return keyId.takeLast(4)
        val digest = MessageDigest.getInstance("SHA-256").digest(encoded.toByteArray(StandardCharsets.UTF_8))
        return HexFormat.of().formatHex(digest).takeLast(4)
    }

    private class Parsed(
        val privateKeyId: String?,
    )

    private fun parse(encoded: String): Parsed? {
        if (encoded.isEmpty() || encoded.length > MAX_ENCODED_CHARS) return null
        val decoded =
            try {
                Base64.getDecoder().decode(encoded)
            } catch (_: IllegalArgumentException) {
                return null
            }
        // 표준 Base64 한 가지 표기만 받는다. 줄바꿈·URL-safe 변형을 받으면 Python 쪽 canonical 검사에서
        // 실행 때 처음 실패한다.
        if (Base64.getEncoder().encodeToString(decoded) != encoded) return null
        return try {
            val root = mapper.readTree(String(decoded, StandardCharsets.UTF_8))

            fun text(name: String): String? =
                root
                    ?.get(name)
                    ?.takeIf { it.isString }
                    ?.stringValue()
            if (
                root == null ||
                !root.isObject ||
                text("type") != "service_account" ||
                text("project_id").isNullOrBlank() ||
                text("client_email").isNullOrBlank() ||
                !text("private_key").orEmpty().contains("PRIVATE KEY") ||
                text("token_uri") != TOKEN_URI
            ) {
                null
            } else {
                Parsed(text("private_key_id"))
            }
        } catch (_: JacksonException) {
            null
        } finally {
            decoded.fill(0)
        }
    }
}
