package com.capstone.decision.infrastructure.mcp

import java.net.URI
import java.nio.charset.StandardCharsets
import java.text.Normalizer

/** SearXNG가 외부 engine에 전달하기 전에 secret·개인식별·계좌·prompt payload를 local에서 거부한다. */
internal fun requirePublicWebQuery(query: String) {
    require(query.isNotBlank() && query.toByteArray(StandardCharsets.UTF_8).size <= 1_024)
    val normalized =
        Normalizer
            .normalize(query, Normalizer.Form.NFKC)
            .lowercase()
            .filterNot { Character.getType(it) == Character.FORMAT.toInt() }
    require(normalized.none { Character.isISOControl(it) })
    require(!PROMPT_INJECTION.containsMatchIn(normalized))
    require(!SECRET_OR_TOKEN.containsMatchIn(normalized))
    require(!PERSONAL_DATA.containsMatchIn(normalized))
}

/** Search result title/snippet은 read 전에도 모델에 보이므로 지시문·비밀 패턴이면 폐기한다. */
internal fun sanitizePublicWebSearchText(
    value: String,
    maximumCharacters: Int,
): String {
    require(maximumCharacters in 1..2_000)
    val normalized =
        Normalizer
            .normalize(value, Normalizer.Form.NFKC)
            .filterNot { Character.isISOControl(it) || Character.getType(it) == Character.FORMAT.toInt() }
            .replace(Regex("\\s+"), " ")
            .trim()
            .take(maximumCharacters)
    return normalized
        .takeUnless {
            PROMPT_INJECTION.containsMatchIn(it) ||
                SECRET_OR_TOKEN.containsMatchIn(it) ||
                PERSONAL_DATA.containsMatchIn(it)
        }.orEmpty()
}

/** Search tool result URL도 모델에 노출되므로 reader와 같은 공개 HTTPS shape로 먼저 정규화한다. */
internal fun normalizePublicWebSearchUrl(value: String): String {
    require(value.length in 1..2_048)
    val uri = URI.create(value).normalize()
    require(uri.scheme == "https" && uri.isAbsolute && uri.host != null)
    require(uri.rawUserInfo == null && uri.rawFragment == null)
    require(uri.port in setOf(-1, 443))
    return uri.toASCIIString()
}

private val PROMPT_INJECTION =
    Regex(
        "(?:ignore|disregard|override|bypass).{0,48}(?:previous|system|developer|instructions?|prompt)|" +
            "(?:system|developer)\\s*(?:message|prompt)|(?:이전|기존).{0,24}지시.{0,24}무시|" +
            "(?:도구|함수|mcp|플러그인).{0,20}(?:호출|실행)|시스템\\s*프롬프트",
        RegexOption.IGNORE_CASE,
    )
private val SECRET_OR_TOKEN =
    Regex(
        "(?:access\\W*token|refresh\\W*token|api\\W*key|client\\W*secret|password|" +
            "비밀|시크릿|토큰|비밀번호|api\\W*키)|\\bbearer\\W+[a-z0-9._~-]{8,}|\\bsk-[a-z0-9_-]{16,}\\b",
        RegexOption.IGNORE_CASE,
    )
private val PERSONAL_DATA =
    Regex(
        "(?:account\\W*(?:number|balance)|계좌\\W*(?:번호|잔고)|주문\\W*(?:내역|번호)|" +
            "체결\\W*(?:내역|번호)|주민\\W*(?:등록)?\\W*번호|전화\\W*번호|이메일\\W*주소)|" +
            "(?<![\\w.+-])[a-z0-9._%+-]{1,64}@[a-z0-9.-]{1,253}\\.[a-z]{2,63}(?![\\w.-])|" +
            "(?<!\\d)01[016789][ -]?\\d{3,4}[ -]?\\d{4}(?!\\d)|" +
            "(?<!\\d)\\d{6}[ -]?[1-4]\\d{6}(?!\\d)",
        RegexOption.IGNORE_CASE,
    )
