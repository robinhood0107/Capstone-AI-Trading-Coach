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
            variants.any(::isAdvice) ->
                blocked(
                    status = RagGenerationStatus.BLOCKED_ADVICE,
                    flag = "PERSONALIZED_TRADING_ADVICE",
                )
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

    private fun isAdvice(value: String): Boolean = ADVICE.containsMatchIn(value)

    private companion object {
        val PROMPT_INJECTION =
            Regex(
                "(ignore\\W*(?:previous|prior)|ignoreprevious|system\\W*prompt|systemprompt|" +
                    "이전\\W*지시.{0,16}무시|시스템\\W*프롬프트|" +
                    "(?:call|invoke|호출|실행).{0,20}(?:tool|function|mcp|도구|함수)|" +
                    "https?://|https3a2f2f)",
                RegexOption.IGNORE_CASE,
            )
        val SENSITIVE =
            Regex(
                "(계좌|잔고|보유종목|보유수량|주문내역|체결내역|연락처|전화번호|이메일|" +
                    "주민번호|access\\W*token|api\\W*key|client\\W*secret|password|" +
                    "holdings?|positions?|orders?|fills?|account\\W*(?:number|balance)|" +
                    "phone\\W*number|email\\W*address|" +
                    "(?<![\\w.+-])[a-z0-9._%+-]{1,64}@[a-z0-9.-]{1,253}\\.[a-z]{2,63}(?![\\w.-])|" +
                    "(?<!\\d)01[016789][ -]?\\d{3,4}[ -]?\\d{4}(?!\\d)|" +
                    "(?<!\\d)\\d{6}[ -]?[1-4]\\d{6}(?!\\d)|" +
                    "\\bbearer\\W+[a-z0-9._~-]{8,}|\\bsk-[a-z0-9_-]{16,}\\b)",
                RegexOption.IGNORE_CASE,
            )
        val ADVICE =
            Regex(
                "((?:내가|나는|저는|제게|내일|지금).{0,24}(?:사야|팔아|매수|매도)|" +
                    "몇\\W*주.{0,16}(?:사|팔|매수|매도)|should\\W*i\\W*(?:buy|sell)|" +
                    "how\\W*many\\W*shares)",
                RegexOption.IGNORE_CASE,
            )
        val BASE64 = Regex("^[A-Za-z0-9+/]+={0,2}$")
        val HTML_NUMERIC_ENTITY = Regex("&#([xX][0-9A-Fa-f]{1,6}|[0-9]{1,7});")
    }
}
