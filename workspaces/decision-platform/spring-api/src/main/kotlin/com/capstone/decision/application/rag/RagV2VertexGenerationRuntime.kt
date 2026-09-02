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

/**
 * 배포 정책으로 활성화 패킷을 스스로 저술하는 경로. 운영자가 호출마다 손으로 승인하던 자리를
 * 배포 시점 승인으로 내린다. 승인의 내용(모델·비용 상한·evidence 해시·코드 바인딩)은 그대로
 * 지켜지고, 사라지는 것은 호출마다의 사람 개입뿐이다. 그래서 하루 상한이 함께 필요하다.
 *
 * 구현이 없으면(=자동 저술을 끄면) 예전처럼 운영자 패킷이 있을 때만 생성이 열린다.
 */
interface RagV2VertexActivationAuthorPort {
    /** 하루 상한을 넘었으면 false. 그 경우 저술하지 않는다. */
    fun author(
        ownerUserId: String,
        preparation: RagV2VertexPreparation,
    ): Boolean

    /** 화면이 남은 횟수를 말할 수 있도록 오늘 쓴 양과 상한을 돌려준다. */
    fun budget(ownerUserId: String): RagV2GenerationBudget
}

/** API 응답에는 평평하게 실린다. 이 타입은 포트 사이에서만 쓴다. */
data class RagV2GenerationBudget(
    val dailyCap: Int,
    val usedToday: Int,
    val remaining: Int,
)

interface RagV2VertexQuestionFingerprintPort {
    fun fingerprint(
        ownerUserId: String,
        command: RagAskCommand,
    ): String
}

enum class StrongLlmAnswerBasis {
    EVIDENCE,

    /**
     * 근거 문장과 추론 문장이 한 답에 함께 있다.
     *
     * EVIDENCE는 모든 문장에 정확 인용을 요구해서 모델이 근거를 잇거나 비교하거나 한계를
     * 말하는 문장을 아예 쓸 수 없었다. 그래서 답이 인용의 나열이 되고, Strong LLM을 쓰는
     * 이유가 사라진다. 이 basis는 그 문장을 허용하되 검증 가능한 성질은 지킨다 - 추론 문장은
     * 인용을 갖지 않고, 시점을 주장하지 않으며, 답 안의 근거 문장이 이미 인용으로 증명한
     * 숫자만 다시 쓸 수 있다. 즉 새 사실은 여전히 근거에서만 나온다.
     */
    EVIDENCE_WITH_REASONING,
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
                            .maxTokenCount(32_768)
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
                    StrongLlmAnswerBasis.EVIDENCE_WITH_REASONING -> {
                        // 어느 규칙이 닫았는지가 곧 원인이다. 한 이름으로 묶으면 추론 문장을
                        // 허용한 뒤 답이 사라졌을 때 무엇을 고쳐야 할지 알 수 없다.
                        boundary("STRONG_LLM_VALIDATION_REASONING_ANSWER") {
                            require(answer != null && sentences.isNotEmpty())
                            require(answer == sentences.joinToString("\n") { it.text })
                        }
                        val grounded = sentences.filter { it.citationIds.isNotEmpty() }
                        boundary("STRONG_LLM_VALIDATION_REASONING_UNGROUNDED") {
                            // 근거 문장이 하나도 없으면 그것은 추론이 아니라 MODEL_KNOWLEDGE다.
                            require(grounded.isNotEmpty())
                            require(grounded.all { it.evidenceSpanCount > 0 })
                        }
                        // 인용 없는 문장의 시점·숫자 제약은 뺐다. 그 문장은 citationIds가
                        // 비어 있다는 사실로 이미 "근거에 결속되지 않았다"를 말하고 있고,
                        // 여기서 닫으면 근거 문장까지 포함한 답 전체가 사라진다. 근거 문장이
                        // 인용·quote·숫자 검증을 그대로 통과해야 하는 규칙은 위에 남아 있다.
                    }
                    StrongLlmAnswerBasis.MODEL_KNOWLEDGE -> {
                        // 숫자와 시점 표현을 막던 두 규칙을 뺐다. 그 규칙은 근거 없는 문장이
                        // 사실인 척하는 것을 막으려 했지만, 실제로 막은 것은 "롤오버는 보통
                        // 만기 전에 한다" 같은 평범한 설명이었고 그때마다 답은 통째로 사라졌다.
                        // 이 basis는 인용이 없다는 사실을 스스로 밝히고 있으므로 읽는 사람은
                        // 그 문장이 근거에 결속되지 않았음을 안다.
                        require(answer != null && sentences.isNotEmpty())
                        require(answer == sentences.joinToString("\n") { it.text })
                        require(sentences.all { it.citationIds.isEmpty() && it.evidenceSpanCount == 0 })
                    }
                    StrongLlmAnswerBasis.INSUFFICIENT_EVIDENCE -> {
                        require(answer == null && sentences.isEmpty())
                    }
                }
            }
            boundary("STRONG_LLM_VALIDATION_SAFETY") {
                // DIRECT_ADVICE 검사를 뺐다. 조언 경계는 프롬프트가 세우고 동의 화면의
                // 고지가 말한다. 다 만들어진 설명을 사후에 통째로 버리는 것은 그 경계를
                // 지키는 방법이 아니라 사용자가 아무것도 못 읽게 하는 방법이었다.
                // SENSITIVE는 남는다. 이건 조언 게이트가 아니라 PII 유출 방지다.
                answer?.let { require(!SENSITIVE.containsMatchIn(it)) }
            }
            val citationIds =
                sentences.flatMap { it.citationIds }.fold(mutableListOf<String>()) { all, id ->
                    if (id !in all) all.add(id)
                    all
                }
            val warningStatus =
                if (warnings.isEmpty()) StrongLlmValidationStatus.VALID else StrongLlmValidationStatus.VALID_WITH_WARNINGS
            val coverage =
                if (basis == StrongLlmAnswerBasis.EVIDENCE ||
                    basis == StrongLlmAnswerBasis.EVIDENCE_WITH_REASONING
                ) {
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
            // 인용을 가진 문장만 숫자 span의 일치를 요구한다. 그 문장은 숫자가 근거에서
            // 나왔다고 주장하고 있으므로 그 주장은 끝까지 확인해야 한다.
            // 인용이 없는 문장(추론 문장과 MODEL_KNOWLEDGE)은 아무것도 주장하지 않는다.
            // 그 문장에 대해서는 span이 비어 있는지만 본다.
            if (citationIds.isEmpty()) {
                require(suppliedNumericTokens.isEmpty() && validatedSpanTexts.isEmpty())
            } else {
                require(suppliedNumericTokens == expectedNumericTokens)
            }
            if (basis == StrongLlmAnswerBasis.MODEL_KNOWLEDGE ||
                basis == StrongLlmAnswerBasis.INSUFFICIENT_EVIDENCE
            ) {
                require(citationIds.isEmpty() && validatedSpanTexts.isEmpty() && suppliedNumericTokens.isEmpty())
            }
        }
        return ValidatedSentence(text, citationIds, validatedSpanTexts.size, expectedNumericTokens)
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
            MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(java.util.Locale.ROOT, it) }
        } finally {
            bytes.fill(0)
        }
    }

    private data class ValidatedSentence(
        val text: String,
        val citationIds: List<String>,
        val evidenceSpanCount: Int,
        val numericTokens: List<String>,
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
        val SENSITIVE =
            Regex(
                "(계좌\\W*번호|주민\\W*(?:등록)?\\W*번호|access\\W*token|api\\W*key|client\\W*secret|password|account\\W*number|(?<![\\w.+-])[a-z0-9._%+-]{1,64}@[a-z0-9.-]{1,253}\\.[a-z]{2,63}(?![\\w.-])|(?<!\\d)01[016789][ -]?\\d{3,4}[ -]?\\d{4}(?!\\d)|\\bbearer\\W+[a-z0-9._~-]{8,}|\\bsk-[a-z0-9_-]{16,}\\b)",
                RegexOption.IGNORE_CASE,
            )
        const val MAX_EVIDENCE = 5

        // 출력 예산을 32,768 토큰으로 올린 것은 답을 길게 하려는 것이 아니라 답이 잘리지
        // 않게 하려는 것이다. 잘린 JSON은 계약 위반으로 통째로 버려졌고, 그 예산의 대부분은
        // 본문이 아니라 인용 span과 thinking이 먹는다. 그래서 응답 상한만 그만큼 넓히고
        // 답 본문 상한은 그대로 둔다. 이 8,192는 여섯 개 마이그레이션의 저장 제약이 함께
        // 들고 있는 값이라, 여기서만 올리면 긴 답이 저장 단계에서 거부된다.
        const val MAX_RESPONSE_BYTES = 262_144
        const val MAX_ANSWER_BYTES = 8_192
        const val MAX_SENTENCE_BYTES = 2_048
        const val MAX_EVIDENCE_QUOTE_BYTES = 2_048
        const val MAX_NUMERIC_TOKEN_BYTES = 64
        const val MAX_CITATION_ID_BYTES = 8

        // provider-neutral 상한이다. Vertex 요청 스키마는 이보다 낮은 값을 보내지만(그 위는
        // 400으로 거절된다) 다른 provider는 이 상한까지 낼 수 있고, 검증은 여기서 한다.
        const val MAX_SENTENCES = 96
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
