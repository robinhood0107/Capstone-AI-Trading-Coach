package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagAskCommand
import com.capstone.decision.application.rag.RagEvaluationContext
import com.capstone.decision.application.rag.RagEvaluationPort
import com.capstone.decision.application.rag.RagEvaluationResult
import com.capstone.decision.application.rag.RagGenerationStatus
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import java.net.URLDecoder
import java.nio.charset.StandardCharsets
import java.text.Normalizer
import java.util.Base64

@Component
@ConditionalOnProperty(
    name = ["app.rag.grpc.enabled"],
    havingValue = "false",
    matchIfMissing = true,
)
class RagFixtureEvaluationAdapter : RagEvaluationPort {
    /**
     * 실제 retrieval/gRPC를 연결하는 S4.6 전에는 local rule만 실행하고 허용 질문도 retrieval-only로 닫는다.
     */
    override fun evaluate(
        command: RagAskCommand,
        context: RagEvaluationContext,
    ): RagEvaluationResult {
        val variants = variants(command.question)
        return when {
            variants.any(::isPromptInjection) ->
                blocked(
                    status = RagGenerationStatus.BLOCKED_SENSITIVE,
                    flag = "PROMPT_INJECTION",
                )
            variants.any(::isSensitive) ->
                blocked(
                    status = RagGenerationStatus.BLOCKED_SENSITIVE,
                    flag = "SENSITIVE_DATA",
                )
            // 조언성 질문을 막던 분기를 뺐다. 개념과 위험은 설명하고, 조언이 아니라는
            // 사실은 동의 고지가 말한다. Python guard와 같은 결정을 여기서도 한다.
            else ->
                RagEvaluationResult(
                    generationStatus = RagGenerationStatus.RETRIEVAL_ONLY,
                    answer = null,
                    citations = emptyList(),
                    citationCoverage = 0.0,
                    retrievalFailure = false,
                    guardrailFlags = listOf("FIXTURE_ONLY"),
                    providerPhysicalAttempts = 0,
                    externalProviderCandidate = false,
                )
        }
    }

    private fun blocked(
        status: RagGenerationStatus,
        flag: String,
    ): RagEvaluationResult =
        RagEvaluationResult(
            generationStatus = status,
            answer = null,
            citations = emptyList(),
            citationCoverage = 0.0,
            retrievalFailure = false,
            guardrailFlags = listOf(flag),
            providerPhysicalAttempts = 0,
            externalProviderCandidate = false,
        )

    private fun variants(question: String): List<String> {
        val decoded = mutableListOf(question, decodeHtmlEntities(question))
        var percentDecoded = question
        repeat(2) {
            val next =
                runCatching { URLDecoder.decode(percentDecoded, StandardCharsets.UTF_8) }
                    .getOrDefault(percentDecoded)
            if (next != percentDecoded) {
                decoded.add(next)
                decoded.add(decodeHtmlEntities(next))
            }
            percentDecoded = next
        }
        decodeBase64(question)?.let(decoded::add)
        return decoded
            .flatMap { value ->
                val normalized = Normalizer.normalize(value, Normalizer.Form.NFKC).lowercase()
                val withoutFormat =
                    normalized.filterNot { character ->
                        Character.getType(character) in
                            setOf(
                                Character.FORMAT.toInt(),
                                Character.SURROGATE.toInt(),
                            )
                    }
                listOf(withoutFormat, withoutFormat.filter(Char::isLetterOrDigit))
            }.distinct()
    }

    private fun decodeBase64(value: String): String? {
        val compact = value.filterNot(Char::isWhitespace)
        if (
            compact.length !in 16..4_096 ||
            compact.length % 4 != 0 ||
            !BASE64.matches(compact)
        ) {
            return null
        }
        return runCatching {
            val bytes = Base64.getDecoder().decode(compact)
            val decoded = bytes.toString(StandardCharsets.UTF_8)
            require(decoded.length in 1..2_048)
            require(decoded.toByteArray(StandardCharsets.UTF_8).contentEquals(bytes))
            decoded
        }.getOrNull()
    }

    private fun decodeHtmlEntities(value: String): String {
        var decoded =
            value
                .replace("&amp;", "&", ignoreCase = true)
                .replace("&lt;", "<", ignoreCase = true)
                .replace("&gt;", ">", ignoreCase = true)
                .replace("&quot;", "\"", ignoreCase = true)
                .replace("&#39;", "'", ignoreCase = true)
        decoded =
            HTML_NUMERIC_ENTITY.replace(decoded) { match ->
                val token = match.groupValues[1]
                val codePoint =
                    if (token.startsWith("x", ignoreCase = true)) {
                        token.drop(1).toIntOrNull(16)
                    } else {
                        token.toIntOrNull()
                    }
                if (
                    codePoint != null &&
                    Character.isValidCodePoint(codePoint) &&
                    codePoint !in 0xD800..0xDFFF
                ) {
                    String(Character.toChars(codePoint))
                } else {
                    match.value
                }
            }
        return decoded
    }

    private fun isPromptInjection(value: String): Boolean = PROMPT_INJECTION.containsMatchIn(value)

    private fun isSensitive(value: String): Boolean = SENSITIVE.containsMatchIn(value)

    private companion object {
        val PROMPT_INJECTION =
            Regex(
                "(ignore\\W*(?:previous|prior)|ignoreprevious|system\\W*prompt|systemprompt|" +
                    "이전\\W*지시.{0,16}무시|시스템\\W*프롬프트|" +
                    "(?:call|invoke|호출|실행).{0,20}(?:tool|function|mcp|도구|함수))",
                RegexOption.IGNORE_CASE,
            )

        // 일반 금융 명사로 질문을 닫지 않는다. 막아야 하는 것은 개념이 아니라 특정인의
        // 자료를 달라는 요구다. `내 `, `제 `는 뒤에 공백을 요구해 `내일`, `제도`를 배제한다.
        val SENSITIVE =
            Regex(
                "(계좌\\W*번호|계좌번호|주민\\W*(?:등록)?\\W*번호|주민번호|" +
                    "access\\W*token|api\\W*key|client\\W*secret|password|" +
                    "account\\W*(?:number|balance)|" +
                    "(?:내\\W+|제\\W+|나의|저의|본인|타인|남의|다른\\W*사용자|다른\\W*사람)" +
                    "\\W*.{0,3}(?:계좌|잔고|보유\\W*종목|보유종목|포지션|주문\\W*내역|주문내역|체결\\W*내역|체결내역)|" +
                    "(?:계좌|잔고|보유\\W*종목|보유종목|주문\\W*내역|주문내역|체결\\W*내역|체결내역)" +
                    ".{0,16}(?:조회|추출|공개|열람|내려받|다운로드|알려|보여)|" +
                    "\\bmy\\W+(?:\\w+\\W+)?(?:account|balance|holdings?|positions?|orders?|fills?)\\b|" +
                    "\\b(?:another|other)\\W+users?\\W+(?:account|balance|holdings?|positions?|orders?)\\b|" +
                    "(?<![\\w.+-])[a-z0-9._%+-]{1,64}@[a-z0-9.-]{1,253}\\.[a-z]{2,63}(?![\\w.-])|" +
                    "(?<!\\d)01[016789][ -]?\\d{3,4}[ -]?\\d{4}(?!\\d)|" +
                    "(?<!\\d)\\d{6}[ -]?[1-4]\\d{6}(?!\\d)|" +
                    "\\bbearer\\W+[a-z0-9._~-]{8,}|\\bsk-[a-z0-9_-]{16,}\\b)",
                RegexOption.IGNORE_CASE,
            )
        val BASE64 = Regex("^[A-Za-z0-9+/]+={0,2}$")
        val HTML_NUMERIC_ENTITY = Regex("&#([xX][0-9A-Fa-f]{1,6}|[0-9]{1,7});")
    }
}
