package com.capstone.decision.application.rag

import tools.jackson.core.JacksonException
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.JsonNode
import tools.jackson.databind.json.JsonMapper
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.text.Normalizer
import java.time.Instant

/**
 * Strong LLM에 일시적으로 전달할 top-5 evidence다. canonical text는 provider response·history·usage ledger에
 * 기록하지 않으며, 현재 request의 owner-scoped DB 검증이 끝난 메모리에서만 존재한다.
 */
data class RagV2VertexEvidence(
    val ordinal: Int,
    val citationId: String,
    val chunkRevisionId: String,
    val canonicalText: String,
    val canonicalTextSha256: String,
    val supportType: StrongLlmEvidenceSupportType = StrongLlmEvidenceSupportType.CANONICAL_EXACT,
    val title: String? = null,
    val canonicalUrl: String? = null,
    val sectionTitle: String? = null,
    val ownerPrivate: Boolean = false,
)

enum class StrongLlmEvidenceSupportType {
    CANONICAL_EXACT,
    GOOGLE_GROUNDING,
}

/** Strong LLM 입력은 retrieval·동의·owner 경계를 통과한 Top-5 전체이며 Decision/Signal/Risk 입력이 아니다. */
data class RagV2VertexGenerationCommand(
    val ownerUserId: String,
    val requestId: String,
    val question: String,
    val answerMode: RagAnswerMode,
    val relatedSymbols: List<String> = emptyList(),
    val topics: List<String> = emptyList(),
    val scope: RagV2RetrievalScope,
    val consent: RagV2EffectiveConsent,
    val evidence: List<RagV2VertexEvidence>,
)

/** content-free provider preparation; raw question/evidence는 packet이나 DB에 저장하지 않는다. */
data class RagV2VertexPreparation(
    val contractId: String = "s4-rag-v2-vertex-preparation-v1",
    val schemaVersion: Int = 1,
    val requestId: String,
    val scopeClaimId: String,
    val questionFingerprintHmac: String,
    val answerMode: RagAnswerMode,
    val embeddingProfileId: String,
    val consentEventId: String,
    val policyDigest: String,
    val processorSetDigest: String,
    val expiresAt: Instant,
    val scopeTtlSeconds: Int = 300,
    val rawQuestionStored: Boolean = false,
    val rawEvidenceStored: Boolean = false,
)

interface RagV2VertexQuestionFingerprintPort {
    fun fingerprint(
        ownerUserId: String,
        command: RagAskCommand,
    ): String
}

enum class StrongLlmAnswerBasis {
    EVIDENCE,
    MODEL_KNOWLEDGE,
    INSUFFICIENT_EVIDENCE,
}

enum class StrongLlmValidationStatus {
    VALID,
    VALID_WITH_WARNINGS,
}

data class StrongLlmGenerationResult(
    val generationStatus: RagGenerationStatus,
    val answer: String?,
    val citationIds: List<String>,
    val failureCode: String,
    val answerBasis: StrongLlmAnswerBasis? = null,
    val validationStatus: StrongLlmValidationStatus? = null,
    val warnings: List<String> = emptyList(),
    val citationCoverage: Double = 0.0,
    val webCitations: List<StrongLlmWebCitation> = emptyList(),
)

data class StrongLlmWebCitation(
    val citationId: String,
    val sourceId: String,
    val title: String,
    val sectionTitle: String,
    val canonicalUrl: String,
    val provenanceResultId: String,
)

/** Provider-neutral final generator port. 구현체가 Vertex여도 application 계층은 특정 모델 transport를 알지 않는다. */
interface StrongLlmGenerationPort {
    fun isActivationEnabled(): Boolean

    fun generate(command: RagV2VertexGenerationCommand): StrongLlmGenerationResult
}

typealias RagV2VertexGenerationPort = StrongLlmGenerationPort
typealias RagV2VertexGenerationResult = StrongLlmGenerationResult

interface RagV2VertexEvidencePort {
    fun resolve(
        ownerUserId: String,
        requestId: String,
        scope: RagV2RetrievalScope,
        citations: List<RagV2RetrievedCitation>,
    ): List<RagV2VertexEvidence>
}

data class StrongLlmValidatedAnswer(
    val basis: StrongLlmAnswerBasis,
    val answer: String?,
    val citationIds: List<String>,
    val validationStatus: StrongLlmValidationStatus,
    val warnings: List<String>,
    val citationCoverage: Double,
)

typealias RagV2VertexValidatedAnswer = StrongLlmValidatedAnswer

/**
 * 생성 결과를 선택하거나 고치지 않고 provenance만 검증한다. EVIDENCE 문장은 의역할 수 있지만 모든 문장에
 * canonical evidence의 exact quote가 필요하며, quote·숫자·citation·owner 경계를 하나라도 위반하면 거부한다.
 */
class RagV2VertexResponseValidator {
    private val mapper =
        JsonMapper
            .builder(
                JsonFactory
                    .builder()
                    .streamReadConstraints(
                        StreamReadConstraints
                            .builder()
                            .maxNestingDepth(8)
                            .maxDocumentLength(MAX_RESPONSE_BYTES.toLong())
                            .maxTokenCount(2_048)
                            .maxNumberLength(32)
                            .maxStringLength(MAX_RESPONSE_BYTES)
                            .maxNameLength(64)
                            .build(),
                    ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                    .build(),
            ).build()

    fun validate(
        rawResponseText: String,
        evidence: List<RagV2VertexEvidence>,
    ): StrongLlmValidatedAnswer {
        try {
            val evidenceByCitationId = boundary("STRONG_LLM_VALIDATION_EVIDENCE") { validateEvidence(evidence) }
            val root = boundary("STRONG_LLM_VALIDATION_JSON") { mapper.readTree(rawResponseText) }
            boundary("STRONG_LLM_VALIDATION_ROOT") { require(root != null && root.isObject) }
            boundary("STRONG_LLM_VALIDATION_ROOT_FIELDS") {
                require(root.properties().map { it.key }.toSet() == ROOT_FIELDS)
            }
            val basis =
                boundary("STRONG_LLM_VALIDATION_BASIS") {
                    StrongLlmAnswerBasis.valueOf(requireText(root.get("basis"), 32, false))
                }
            val answer = boundary("STRONG_LLM_VALIDATION_ANSWER") { nullableText(root.get("answer"), MAX_ANSWER_BYTES, true) }
            val warnings = boundary("STRONG_LLM_VALIDATION_WARNINGS") { requireWarnings(root.get("warnings")) }
            val sentencesNode = root.get("sentences")
            boundary("STRONG_LLM_VALIDATION_SENTENCES") {
                require(sentencesNode != null && sentencesNode.isArray && sentencesNode.size() <= MAX_SENTENCES)
            }
            val sentences =
                sentencesNode
                    .values()
                    .asSequence()
                    .mapIndexed { index, node ->
                        boundary("STRONG_LLM_VALIDATION_SENTENCE_${index + 1}") {
                            validateSentence(node, basis, evidenceByCitationId)
                        }
                    }.toList()

            boundary("STRONG_LLM_VALIDATION_BASIS_CONTRACT") {
                when (basis) {
                    StrongLlmAnswerBasis.EVIDENCE -> {
                        require(answer != null && sentences.isNotEmpty())
                        require(answer == sentences.joinToString("\n") { it.text })
                        require(sentences.all { it.citationIds.isNotEmpty() && it.evidenceSpanCount > 0 })
                    }
                    StrongLlmAnswerBasis.MODEL_KNOWLEDGE -> {
                        require(answer != null && sentences.isNotEmpty())
                        require(warnings.isEmpty())
                        require(answer == sentences.joinToString("\n") { it.text })
                        require(sentences.all { it.citationIds.isEmpty() && it.evidenceSpanCount == 0 })
                        require(sentences.none { NUMERIC_TOKEN.containsMatchIn(it.text) || CURRENT_FACT.containsMatchIn(it.text) })
                    }
                    StrongLlmAnswerBasis.INSUFFICIENT_EVIDENCE -> {
                        require(answer == null && sentences.isEmpty())
                    }
                }
            }
            boundary("STRONG_LLM_VALIDATION_SAFETY") {
                answer?.let {
                    require(!SENSITIVE.containsMatchIn(it))
                    require(!DIRECT_ADVICE.containsMatchIn(it))
                }
            }
            val citationIds =
                sentences.flatMap { it.citationIds }.fold(mutableListOf<String>()) { all, id ->
                    if (id !in all) all.add(id)
                    all
                }
            val warningStatus =
                if (warnings.isEmpty()) StrongLlmValidationStatus.VALID else StrongLlmValidationStatus.VALID_WITH_WARNINGS
            val coverage =
                if (basis == StrongLlmAnswerBasis.EVIDENCE) {
                    sentences.count { it.evidenceSpanCount > 0 }.toDouble() / sentences.size
                } else {
                    0.0
                }
            return StrongLlmValidatedAnswer(basis, answer, citationIds, warningStatus, warnings, coverage)
        } catch (error: RagV2VertexResponseValidationException) {
            throw error
        } catch (_: JacksonException) {
            throw RagV2VertexResponseValidationException("STRONG_LLM_VALIDATION_JSON")
        } catch (_: IllegalArgumentException) {
            throw RagV2VertexResponseValidationException("STRONG_LLM_VALIDATION_UNKNOWN")
        } catch (_: IllegalStateException) {
            throw RagV2VertexResponseValidationException("STRONG_LLM_VALIDATION_UNKNOWN")
        }
    }

    private fun validateEvidence(evidence: List<RagV2VertexEvidence>): Map<String, RagV2VertexEvidence> {
        require(evidence.size in 0..MAX_EVIDENCE)
        require(evidence.map { it.ordinal } == (1..evidence.size).toList())
        require(evidence.map { it.citationId }.distinct().size == evidence.size)
        require(
            evidence.all {
                CITATION_ID.matches(it.citationId) &&
                    CHUNK_ID.matches(it.chunkRevisionId) &&
                    SHA256.matches(it.canonicalTextSha256) &&
                    sha256(it.canonicalText) == it.canonicalTextSha256
            },
        )
        return evidence.associateBy { it.citationId }
    }

    private fun validateSentence(
        node: JsonNode,
        basis: StrongLlmAnswerBasis,
        evidenceByCitationId: Map<String, RagV2VertexEvidence>,
    ): ValidatedSentence {
        boundary("STRONG_LLM_VALIDATION_SENTENCE_FIELDS") {
            require(node.isObject && node.properties().map { it.key }.toSet() == SENTENCE_FIELDS)
        }
        val text = boundary("STRONG_LLM_VALIDATION_SENTENCE_TEXT") { requireText(node.get("text"), MAX_SENTENCE_BYTES, false) }
        val citationIds =
            boundary("STRONG_LLM_VALIDATION_SENTENCE_CITATIONS") {
                requireCitationIds(
                    node.get("citationIds"),
                    evidenceByCitationId.keys,
                    allowEmpty = basis != StrongLlmAnswerBasis.EVIDENCE,
                )
            }
        val evidenceSpans = node.get("evidenceSpans")
        boundary("STRONG_LLM_VALIDATION_EVIDENCE_SPANS") {
            require(evidenceSpans != null && evidenceSpans.isArray && evidenceSpans.size() <= MAX_EVIDENCE_SPANS)
        }
        val validatedSpanTexts =
            evidenceSpans
                .values()
                .asSequence()
                .map { span ->
                    boundary("STRONG_LLM_VALIDATION_EVIDENCE_SPAN") {
                        validateEvidenceSpan(span, citationIds, evidenceByCitationId)
                    }
                }.toList()
        boundary("STRONG_LLM_VALIDATION_EVIDENCE_BINDING") {
            require(validatedSpanTexts.map { it.citationId }.toSet() == citationIds.toSet())
        }
        val numericSpans = node.get("numericSpans")
        boundary("STRONG_LLM_VALIDATION_NUMERIC_SPANS") {
            require(numericSpans != null && numericSpans.isArray && numericSpans.size() <= MAX_NUMERIC_SPANS)
        }
        val expectedNumericTokens = NUMERIC_TOKEN.findAll(text).map { it.value }.toList()
        val suppliedNumericTokens =
            numericSpans
                .values()
                .asSequence()
                .map { numeric ->
                    boundary("STRONG_LLM_VALIDATION_NUMERIC_SPAN") {
                        validateNumericSpan(numeric, citationIds, validatedSpanTexts)
                    }
                }.toList()
        boundary("STRONG_LLM_VALIDATION_NUMERIC_BINDING") {
            require(suppliedNumericTokens == expectedNumericTokens)
            if (basis != StrongLlmAnswerBasis.EVIDENCE) {
                require(citationIds.isEmpty() && validatedSpanTexts.isEmpty() && suppliedNumericTokens.isEmpty())
            }
        }
        return ValidatedSentence(text, citationIds, validatedSpanTexts.size)
    }

    private inline fun <T> boundary(
        leaf: String,
        block: () -> T,
    ): T =
        try {
            block()
        } catch (error: RagV2VertexResponseValidationException) {
            throw error
        } catch (_: JacksonException) {
            throw RagV2VertexResponseValidationException(leaf)
        } catch (_: IllegalArgumentException) {
            throw RagV2VertexResponseValidationException(leaf)
        } catch (_: IllegalStateException) {
            throw RagV2VertexResponseValidationException(leaf)
        }

    private fun validateEvidenceSpan(
        node: JsonNode,
        sentenceCitationIds: List<String>,
        evidenceByCitationId: Map<String, RagV2VertexEvidence>,
    ): ValidatedEvidenceSpan {
        require(node.isObject && node.properties().map { it.key }.toSet() == EVIDENCE_SPAN_FIELDS)
        val citationId = requireText(node.get("citationId"), MAX_CITATION_ID_BYTES, false)
        require(citationId in sentenceCitationIds)
        val quote = requireText(node.get("quote"), MAX_EVIDENCE_QUOTE_BYTES, false)
        require(evidenceByCitationId.getValue(citationId).canonicalText.contains(quote))
        return ValidatedEvidenceSpan(citationId, quote)
    }

    private fun validateNumericSpan(
        node: JsonNode,
        sentenceCitationIds: List<String>,
        evidenceSpans: List<ValidatedEvidenceSpan>,
    ): String {
        require(node.isObject && node.properties().map { it.key }.toSet() == NUMERIC_SPAN_FIELDS)
        val value = requireText(node.get("value"), MAX_NUMERIC_TOKEN_BYTES, false)
        require(NUMERIC_TOKEN.matches(value))
        val numericCitationIds = requireCitationIds(node.get("citationIds"), sentenceCitationIds.toSet(), allowEmpty = false)
        require(
            numericCitationIds.all { citationId ->
                evidenceSpans
                    .filter { it.citationId == citationId }
                    .any { span -> NUMERIC_TOKEN.findAll(span.quote).any { it.value == value } }
            },
        )
        return value
    }

    private fun requireWarnings(node: JsonNode?): List<String> {
        require(node != null && node.isArray && node.size() <= MAX_WARNINGS)
        return node.values().asSequence().map { requireText(it, 64, false) }.toList().also {
            require(it.distinct().size == it.size)
            require(it.all(ALLOWED_WARNINGS::contains))
        }
    }

    private fun requireCitationIds(
        node: JsonNode?,
        allowed: Set<String>,
        allowEmpty: Boolean,
    ): List<String> {
        require(node != null && node.isArray && node.size() <= MAX_EVIDENCE)
        if (!allowEmpty) require(node.size() > 0)
        return node.values().asSequence().map { requireText(it, MAX_CITATION_ID_BYTES, false) }.toList().also {
            require(it.distinct().size == it.size && it.all(allowed::contains))
        }
    }

    private fun nullableText(
        node: JsonNode?,
        maximumBytes: Int,
        allowNewline: Boolean,
    ): String? {
        require(node != null)
        return if (node.isNull) null else requireText(node, maximumBytes, allowNewline)
    }

    private fun requireText(
        node: JsonNode?,
        maximumBytes: Int,
        allowNewline: Boolean,
    ): String {
        require(node != null && node.isString)
        val value = node.stringValue()
        require(value.isNotBlank() && Normalizer.normalize(value, Normalizer.Form.NFC) == value)
        require(value.toByteArray(StandardCharsets.UTF_8).size <= maximumBytes)
        require(value.none { Character.isISOControl(it) && (it != '\n' || !allowNewline) })
        require(allowNewline || '\n' !in value)
        return value
    }

    private fun sha256(value: String): String {
        val bytes = value.toByteArray(StandardCharsets.UTF_8)
        return try {
            MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
        } finally {
            bytes.fill(0)
        }
    }

    private data class ValidatedSentence(
        val text: String,
        val citationIds: List<String>,
        val evidenceSpanCount: Int,
    )

    private data class ValidatedEvidenceSpan(
        val citationId: String,
        val quote: String,
    )

    private companion object {
        val ROOT_FIELDS = setOf("basis", "answer", "sentences", "warnings")
        val SENTENCE_FIELDS = setOf("text", "citationIds", "evidenceSpans", "numericSpans")
        val EVIDENCE_SPAN_FIELDS = setOf("citationId", "quote")
        val NUMERIC_SPAN_FIELDS = setOf("value", "citationIds")
        val ALLOWED_WARNINGS =
            setOf(
                "SINGLE_SOURCE",
                "STALE_SOURCE",
                "CONFLICTING_SOURCES",
                "LOW_RELEVANCE",
                "SECONDARY_SOURCE",
                "GOOGLE_GROUNDING_ONLY",
            )
        val CITATION_ID = Regex("^cit_[1-5]$")
        val CHUNK_ID = Regex("^rag_v2_chk_[0-9a-f]{32}$")
        val SHA256 = Regex("^[0-9a-f]{64}$")

        // 한국어 조사(예: `5%를`) 앞에서도 단위까지 한 token으로 잡되 영문 식별자 내부 숫자는 거부한다.
        val NUMERIC_TOKEN =
            Regex(
                "(?<![\\p{L}\\p{N}])[-+]?(?:\\d{1,3}(?:,\\d{3})*|\\d+)(?:\\.\\d+)?(?:%|bp|bps|USD|KRW|원|달러|년|개월|일|주)?(?=$|[^\\p{L}\\p{N}]|[을를이가은는의와과로에])",
            )
        val CURRENT_FACT = Regex("(현재|오늘|최근|최신|금일|this\\s+(?:year|month|week)|today|currently|latest|as\\s+of)", RegexOption.IGNORE_CASE)
        val SENSITIVE =
            Regex(
                "(계좌\\W*번호|주민\\W*(?:등록)?\\W*번호|access\\W*token|api\\W*key|client\\W*secret|password|account\\W*number|(?<![\\w.+-])[a-z0-9._%+-]{1,64}@[a-z0-9.-]{1,253}\\.[a-z]{2,63}(?![\\w.-])|(?<!\\d)01[016789][ -]?\\d{3,4}[ -]?\\d{4}(?!\\d)|\\bbearer\\W+[a-z0-9._~-]{8,}|\\bsk-[a-z0-9_-]{16,}\\b)",
                RegexOption.IGNORE_CASE,
            )
        val DIRECT_ADVICE =
            Regex(
                "((?:내가|나는|저는|제게|내일|지금).{0,24}(?:사야|팔아|매수|매도)|몇\\W*주.{0,16}(?:사|팔|매수|매도)|(?:매수|매도|매입|매각|현금화|비중확대|비중축소).{0,20}(?:하세요|하라|해라|해야|추천|권고)|(?:you|investors?)\\W+(?:should|must|need\\W+to|ought\\W+to|consider)\\W+(?:buy|sell|invest|hold|trade)|(?:recommend|advise)\\W*(?:buying|selling|investing|buy|sell|invest))",
                RegexOption.IGNORE_CASE,
            )
        const val MAX_EVIDENCE = 5
        const val MAX_RESPONSE_BYTES = 32_768
        const val MAX_ANSWER_BYTES = 8_192
        const val MAX_SENTENCE_BYTES = 2_048
        const val MAX_EVIDENCE_QUOTE_BYTES = 2_048
        const val MAX_NUMERIC_TOKEN_BYTES = 64
        const val MAX_CITATION_ID_BYTES = 8
        const val MAX_SENTENCES = 24
        const val MAX_EVIDENCE_SPANS = 12
        const val MAX_NUMERIC_SPANS = 64
        const val MAX_WARNINGS = 5
    }
}

class RagV2VertexResponseValidationException(
    leaf: String = "STRONG_LLM_VALIDATION_UNKNOWN",
) : RuntimeException(leaf)

class RagV2VertexEvidenceUnavailableException : RuntimeException()

class RagV2VertexPreparationUnavailableException : RuntimeException()
