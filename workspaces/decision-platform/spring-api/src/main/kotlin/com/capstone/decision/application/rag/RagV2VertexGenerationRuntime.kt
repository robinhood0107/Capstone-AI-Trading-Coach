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
 * Vertex에 일시적으로 전달할 top-5 evidence다. canonical text는 provider response·history·usage ledger에
 * 기록하지 않으며, DB SECURITY DEFINER 재검증을 통과한 현재 request 범위에서만 존재한다.
 */
data class RagV2VertexEvidence(
    val ordinal: Int,
    val citationId: String,
    val chunkRevisionId: String,
    val canonicalText: String,
    val canonicalTextSha256: String,
)

/**
 * Vertex 경로는 BGE gRPC evaluation 뒤에만 실행된다. owner identity는 usage lease와 DB scope 검증에만 쓰고
 * Vertex HTTP body에는 넣지 않는다.
 */
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

/**
 * Vertex physical-call packet을 만들기 직전에 authenticated owner에게만 주는 content-free preparation이다.
 * question/evidence/owner identity는 저장하거나 응답에 넣지 않고, 2분 retrieval claim과 HMAC만 packet을
 * stable하게 결속한다.
 */
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
    val scopeTtlSeconds: Int = 120,
    val rawQuestionStored: Boolean = false,
    val rawEvidenceStored: Boolean = false,
)

/**
 * ask HMAC은 packet과 append-only usage ledger가 같은 raw-content-free binding을 쓰게 한다.
 * 질문, 답변 mode, related symbol, topic의 canonical command 전체를 결박하고 digest 원문만 반환한다.
 */
interface RagV2VertexQuestionFingerprintPort {
    fun fingerprint(
        ownerUserId: String,
        command: RagAskCommand,
    ): String
}

data class RagV2VertexGenerationResult(
    val generationStatus: RagGenerationStatus,
    val answer: String?,
    val citationIds: List<String>,
    val failureCode: String,
)

/**
 * BGE retrieval transport와 독립된 single-generator port다. 구현체는 local activation packet·consent·DB lease를
 * 각각 재검증하고 `generateContent` 외 provider fallback을 만들지 않는다.
 */
interface RagV2VertexGenerationPort {
    fun isActivationEnabled(): Boolean

    fun generate(command: RagV2VertexGenerationCommand): RagV2VertexGenerationResult
}

/**
 * citation identity를 현재 immutable scope의 canonical text로 재해석하는 DB 경계다. 조회 결과는 external
 * generation 직후 폐기되며 history persistence에는 citation identity만 다시 전달한다.
 */
interface RagV2VertexEvidencePort {
    fun resolve(
        ownerUserId: String,
        requestId: String,
        scope: RagV2RetrievalScope,
        citations: List<RagV2RetrievedCitation>,
    ): List<RagV2VertexEvidence>
}

data class RagV2VertexValidatedAnswer(
    val answer: String,
    val citationIds: List<String>,
)

/**
 * provider 출력은 raw 상태로 신뢰하거나 저장하지 않는다. 모든 문장을 immutable top-5 citation에 결속하고
 * numeric token까지 같은 evidence set으로 재검증해 malformed output을 `GENERATION_UNAVAILABLE`로 닫는다.
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
                            .maxNestingDepth(6)
                            .maxDocumentLength(MAX_RESPONSE_BYTES.toLong())
                            .maxTokenCount(512)
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
    ): RagV2VertexValidatedAnswer {
        try {
            require(evidence.size in 1..MAX_EVIDENCE)
            require(evidence.map { it.citationId }.distinct().size == evidence.size)
            require(evidence.map { it.ordinal } == (1..evidence.size).toList())
            require(
                evidence.all {
                    CITATION_ID.matches(it.citationId) &&
                        CHUNK_ID.matches(it.chunkRevisionId) &&
                        SHA256.matches(it.canonicalTextSha256) &&
                        sha256(it.canonicalText) == it.canonicalTextSha256
                },
            )
            val evidenceByCitationId = evidence.associateBy { it.citationId }
            val root = mapper.readTree(rawResponseText)
            require(root != null && root.isObject)
            require(root.properties().map { it.key }.toSet() == ROOT_FIELDS)
            val answer = requireText(root.get("answer"), MAX_ANSWER_BYTES, allowNewline = true)
            val sentencesNode = root.get("sentences")
            require(sentencesNode != null && sentencesNode.isArray && sentencesNode.size() in 1..MAX_SENTENCES)
            val sentences =
                sentencesNode
                    .values()
                    .asSequence()
                    .map { sentence -> validateSentence(sentence, evidenceByCitationId) }
                    .toList()
            require(answer == sentences.joinToString("\n") { it.text })
            require(!SENSITIVE.containsMatchIn(answer))
            require(!DIRECT_ADVICE.containsMatchIn(answer))
            val citationIds =
                buildList {
                    sentences.forEach { sentence ->
                        sentence.citationIds.forEach { citationId ->
                            if (citationId !in this) {
                                add(citationId)
                            }
                        }
                    }
                }
            require(citationIds.isNotEmpty())
            return RagV2VertexValidatedAnswer(answer = answer, citationIds = citationIds)
        } catch (_: JacksonException) {
            throw RagV2VertexResponseValidationException()
        } catch (_: IllegalArgumentException) {
            throw RagV2VertexResponseValidationException()
        } catch (_: IllegalStateException) {
            throw RagV2VertexResponseValidationException()
        }
    }

    private fun validateSentence(
        node: JsonNode,
        evidenceByCitationId: Map<String, RagV2VertexEvidence>,
    ): ValidatedSentence {
        require(node.isObject)
        require(node.properties().map { it.key }.toSet() == SENTENCE_FIELDS)
        val text = requireText(node.get("text"), MAX_SENTENCE_BYTES, allowNewline = false)
        val citationIds = requireCitationIds(node.get("citationIds"), evidenceByCitationId.keys)
        // 생성 모델의 추론을 별도 verifier로 다시 호출하지 않는다. 대신 출력 문장은 인용한 canonical text의
        // 완결 문장과 정확히 일치해야 하므로, citation ID만 붙인 임의 사실·수치 환각을 fail-closed한다.
        require(
            citationIds.any { citationId ->
                canonicalSentences(evidenceByCitationId.getValue(citationId).canonicalText).contains(normalizeSentence(text))
            },
        )
        val numericSpans = node.get("numericSpans")
        require(numericSpans != null && numericSpans.isArray && numericSpans.size() <= MAX_NUMERIC_SPANS)
        val expectedNumericTokens = NUMERIC_TOKEN.findAll(text).map { it.value }.toList()
        val suppliedNumericTokens =
            numericSpans
                .values()
                .asSequence()
                .map { span -> validateNumericSpan(span, citationIds, evidenceByCitationId) }
                .toList()
        require(suppliedNumericTokens == expectedNumericTokens)
        return ValidatedSentence(text = text, citationIds = citationIds)
    }

    private fun validateNumericSpan(
        node: JsonNode,
        sentenceCitationIds: List<String>,
        evidenceByCitationId: Map<String, RagV2VertexEvidence>,
    ): String {
        require(node.isObject)
        require(node.properties().map { it.key }.toSet() == NUMERIC_SPAN_FIELDS)
        val value = requireText(node.get("value"), MAX_NUMERIC_TOKEN_BYTES, allowNewline = false)
        require(NUMERIC_TOKEN.matches(value))
        val citationIds = requireCitationIds(node.get("citationIds"), sentenceCitationIds.toSet())
        require(
            citationIds.any { citationId ->
                NUMERIC_TOKEN.findAll(evidenceByCitationId.getValue(citationId).canonicalText).any { it.value == value }
            },
        )
        return value
    }

    private fun canonicalSentences(canonicalText: String): Set<String> =
        canonicalText
            .split(SENTENCE_BOUNDARY)
            .asSequence()
            .map(::normalizeSentence)
            .filter { it.isNotEmpty() }
            .toSet()

    private fun normalizeSentence(value: String): String =
        Normalizer
            .normalize(value, Normalizer.Form.NFC)
            .replace(WHITESPACE, " ")
            .trim()

    private fun requireCitationIds(
        node: JsonNode?,
        allowedCitationIds: Set<String>,
    ): List<String> {
        require(node != null && node.isArray && node.size() in 1..MAX_EVIDENCE)
        val citationIds =
            node
                .values()
                .asSequence()
                .map { value -> requireText(value, MAX_CITATION_ID_BYTES, allowNewline = false) }
                .toList()
        require(citationIds.distinct().size == citationIds.size)
        require(citationIds.all { it in allowedCitationIds })
        return citationIds
    }

    private fun requireText(
        node: JsonNode?,
        maximumBytes: Int,
        allowNewline: Boolean,
    ): String {
        require(node != null && node.isString)
        val value = node.stringValue()
        require(value.isNotBlank())
        require(Normalizer.normalize(value, Normalizer.Form.NFC) == value)
        require(value.toByteArray(StandardCharsets.UTF_8).size <= maximumBytes)
        require(value.none { character -> Character.isISOControl(character) && (character != '\n' || !allowNewline) })
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
    )

    private companion object {
        val ROOT_FIELDS = setOf("answer", "sentences")
        val SENTENCE_FIELDS = setOf("text", "citationIds", "numericSpans")
        val NUMERIC_SPAN_FIELDS = setOf("value", "citationIds")
        val CITATION_ID = Regex("^cit_[1-5]$")
        val CHUNK_ID = Regex("^rag_v2_chk_[0-9a-f]{32}$")
        val SHA256 = Regex("^[0-9a-f]{64}$")
        val NUMERIC_TOKEN =
            Regex("(?<![\\p{L}\\p{N}])[-+]?(?:\\d{1,3}(?:,\\d{3})*|\\d+)(?:\\.\\d+)?(?:%|bp|bps|USD|KRW|원|달러|년|개월|일|주)?(?![\\p{L}\\p{N}])")
        val SENTENCE_BOUNDARY = Regex("(?<=[.!?。！？])\\s+|\\R+")
        val WHITESPACE = Regex("\\s+")
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
        val DIRECT_ADVICE =
            Regex(
                "((?:내가|나는|저는|제게|내일|지금).{0,24}(?:사야|팔아|매수|매도)|" +
                    "몇\\W*주.{0,16}(?:사|팔|매수|매도)|" +
                    "(?:이|그|해당)\\W*(?:종목|주식).{0,40}(?:매수|매도|매입|매각|사다|팔다).{0,20}(?:하세요|하라|해라|해야|추천|권고|좋습니다)|" +
                    "(?:매수|매도|매입|매각|현금화|비중확대|비중축소).{0,20}(?:하세요|하라|해라|해야|추천|권고|좋습니다)|(?:사|팔)세요|" +
                    "(?:이|그|해당)\\W*(?:종목|주식|etf|펀드).{0,48}(?:사(?:다|는|야|세요)|팔(?:다|는|아|세요)|" +
                    "매수|매도|매입|매각|투자|보유|편입|청산|공매도).{0,24}(?:하세요|하라|해라|해야|추천|권고|좋습니다|필요)|" +
                    "(?:buy|sell|acquire|purchase|dispose|liquidate|invest|hold|allocate|short|long|rebalance|trade)" +
                    "(?:\\W+(?:in|into|on))?\\W+(?:this|the|now|immediately|shares?|stock|etf|fund|portfolio)|" +
                    "(?:you|investors?)\\W+(?:should|must|need\\W+to|ought\\W+to|consider|avoid|" +
                    "are\\W+advised\\W+to)\\W+(?:buy|sell|acquire|purchase|dispose|liquidate|invest|hold|allocate|short|long|trade)|" +
                    "(?:recommend|recommendation|advise|advisable|consider)\\W*(?:buying|selling|acquiring|purchasing|" +
                    "disposing|investing|holding|buy|sell|acquire|purchase|dispose|invest|hold)|" +
                    "(?:go\\W+long|go\\W+short|liquidate|buy|sell)\\W*[!.]?$)",
                RegexOption.IGNORE_CASE,
            )
        const val MAX_EVIDENCE = 5
        const val MAX_RESPONSE_BYTES = 16_384
        const val MAX_ANSWER_BYTES = 8_192
        const val MAX_SENTENCE_BYTES = 1_024
        const val MAX_NUMERIC_TOKEN_BYTES = 64
        const val MAX_CITATION_ID_BYTES = 8
        const val MAX_SENTENCES = 24
        const val MAX_NUMERIC_SPANS = 64
    }
}

class RagV2VertexResponseValidationException : RuntimeException()

class RagV2VertexEvidenceUnavailableException : RuntimeException()

class RagV2VertexPreparationUnavailableException : RuntimeException()
