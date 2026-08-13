package com.capstone.decision.api.rag

import com.capstone.decision.application.rag.RagAnswerMode
import com.capstone.decision.application.rag.RagAskCommand
import com.capstone.decision.application.rag.RagFieldViolation
import com.capstone.decision.application.rag.RagV2ExternalConsentCommand
import com.capstone.decision.application.rag.RagValidationException
import jakarta.servlet.http.HttpServletRequest
import org.springframework.stereotype.Component
import tools.jackson.core.JacksonException
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.JsonNode
import tools.jackson.databind.json.JsonMapper
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.text.Normalizer
import java.util.Collections
import java.util.HexFormat

@Component
class RagRequestParser {
    private val strictMapper =
        JsonMapper
            .builder(
                JsonFactory
                    .builder()
                    .streamReadConstraints(
                        StreamReadConstraints
                            .builder()
                            .maxNestingDepth(4)
                            .maxDocumentLength(MAX_ASK_BYTES.toLong())
                            .maxTokenCount(64)
                            .maxNumberLength(16)
                            .maxStringLength(MAX_ASK_BYTES)
                            .maxNameLength(64)
                            .build(),
                    ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                    .build(),
            ).build()

    /**
     * public ask body의 네 field만 coercion 없이 읽고 NFC/scalar/UTF-8/list 계약을 같은 경계에서 검증한다.
     */
    fun parseAsk(body: String): RagAskCommand {
        val root = parseObject(body)
        val violations = mutableListOf<RagFieldViolation>()
        rejectUnknown(root, ASK_FIELDS, violations)
        val question = requiredString(root, "question", violations)
        if (
            question != null &&
            (
                Normalizer.normalize(question, Normalizer.Form.NFC) != question ||
                    question.codePointCount(0, question.length) !in 1..1_000 ||
                    question.toByteArray(StandardCharsets.UTF_8).size > MAX_QUESTION_BYTES ||
                    question.any(Char::isSurrogate) ||
                    question.codePoints().anyMatch(Character::isISOControl)
            )
        ) {
            violations.add(RagFieldViolation("/question", "INVALID_FORMAT"))
        }
        val answerMode =
            requiredString(root, "answerMode", violations)?.let { value ->
                runCatching { RagAnswerMode.valueOf(value) }.getOrNull()
                    ?: run {
                        violations.add(RagFieldViolation("/answerMode", "INVALID_ENUM"))
                        null
                    }
            }
        val relatedSymbols =
            stringArray(
                root = root,
                field = "relatedSymbols",
                allowed = null,
                pattern = SYMBOL,
                violations = violations,
            )
        val topics =
            stringArray(
                root = root,
                field = "topics",
                allowed = TOPICS,
                pattern = null,
                violations = violations,
            )
        throwIfInvalid(violations)
        return RagAskCommand(
            question = requireNotNull(question),
            answerMode = requireNotNull(answerMode),
            relatedSymbols = relatedSymbols,
            topics = topics,
        )
    }

    fun requireIdempotencyKey(value: String?): String {
        if (value == null || !IDEMPOTENCY_KEY.matches(value)) {
            throw RagValidationException(
                listOf(RagFieldViolation("/headers/X-Idempotency-Key", "INVALID_FORMAT")),
            )
        }
        return value
    }

    fun parseAnswerId(value: String): String {
        if (!ANSWER_ID.matches(value)) {
            throw RagValidationException(
                listOf(RagFieldViolation("/path/answerId", "INVALID_FORMAT")),
            )
        }
        return value
    }

    fun parseV2AnswerId(value: String): String {
        if (!V2_ANSWER_ID.matches(value)) {
            throw RagValidationException(
                listOf(RagFieldViolation("/path/answerId", "INVALID_FORMAT")),
            )
        }
        return value
    }

    fun parseFeedback(body: String): Boolean {
        val root = parseObject(body)
        val violations = mutableListOf<RagFieldViolation>()
        rejectUnknown(root, setOf("helpful"), violations)
        val helpful = root.get("helpful")
        if (helpful == null) {
            violations.add(RagFieldViolation("/helpful", "REQUIRED"))
        } else if (!helpful.isBoolean) {
            violations.add(RagFieldViolation("/helpful", "INVALID_FORMAT"))
        }
        throwIfInvalid(violations)
        return requireNotNull(helpful).booleanValue()
    }

    fun parseConsent(body: String): RagConsentCommand {
        val root = parseObject(body)
        val violations = mutableListOf<RagFieldViolation>()
        rejectUnknown(root, CONSENT_FIELDS, violations)
        val consentType = requiredString(root, "consentType", violations)
        val action = requiredString(root, "action", violations)
        val policyVersion = requiredString(root, "policyVersion", violations)
        if (consentType != "EXTERNAL_AI_RAG_V1") {
            violations.add(RagFieldViolation("/consentType", "INVALID_ENUM"))
        }
        if (action !in setOf("GRANT", "REVOKE")) {
            violations.add(RagFieldViolation("/action", "INVALID_ENUM"))
        }
        if (policyVersion != "EXTERNAL_AI_RAG_V1") {
            violations.add(RagFieldViolation("/policyVersion", "INVALID_ENUM"))
        }
        throwIfInvalid(violations)
        return RagConsentCommand(
            action = requireNotNull(action),
            policyVersion = requireNotNull(policyVersion),
        )
    }

    /**
     * v2 external processor consent는 exact contract field만 받아 server-owned event identity와 분리한다.
     */
    fun parseV2ExternalConsent(body: String): RagV2ExternalConsentCommand {
        val root = parseObject(body)
        val violations = mutableListOf<RagFieldViolation>()
        rejectUnknown(root, V2_EXTERNAL_CONSENT_FIELDS, violations)
        requireExactString(
            root = root,
            field = "contractId",
            expected = "s4-rag-v2-external-consent-v1",
            violations = violations,
        )
        requireSchemaVersionOne(root, violations)
        requireExactString(
            root = root,
            field = "consentType",
            expected = "EXTERNAL_AI_RAG_V2",
            violations = violations,
        )
        val action = requiredString(root, "action", violations)
        if (action != null && action !in setOf("GRANT", "REVOKE")) {
            violations.add(RagFieldViolation("/action", "INVALID_ENUM"))
        }
        val disclosureDigest = requiredHexDigest(root, "disclosureDigest", violations)
        val policyDigest = requiredHexDigest(root, "policyDigest", violations)
        val processorSetDigest = requiredHexDigest(root, "processorSetDigest", violations)
        throwIfInvalid(violations)
        return RagV2ExternalConsentCommand(
            action = requireNotNull(action),
            disclosureDigest = requireNotNull(disclosureDigest),
            policyDigest = requireNotNull(policyDigest),
            processorSetDigest = requireNotNull(processorSetDigest),
        )
    }

    /**
     * import ticket request는 owner가 명시한 library embedding profile만 반환한다.
     * server default나 fallback을 두지 않아 provider 선택이 ticket 발급 전에 고정된다.
     */
    fun parseV2ImportTicketRequest(body: String): String {
        val root = parseObject(body)
        val violations = mutableListOf<RagFieldViolation>()
        rejectUnknown(root, V2_IMPORT_TICKET_FIELDS, violations)
        requireExactString(
            root = root,
            field = "contractId",
            expected = "s4-rag-v2-import-ticket-request-v2",
            violations = violations,
        )
        requireSchemaVersion(root, expected = 2, violations)
        requireExactString(
            root = root,
            field = "sourceScope",
            expected = "OWNER_PRIVATE",
            violations = violations,
        )
        requireExactString(
            root = root,
            field = "importMode",
            expected = "LOCAL_EPHEMERAL_PARSE",
            violations = violations,
        )
        val embeddingProfileId = requiredString(root, "embeddingProfileId", violations)
        if (embeddingProfileId != null && embeddingProfileId !in OWNER_EMBEDDING_PROFILES) {
            violations.add(RagFieldViolation("/embeddingProfileId", "INVALID_ENUM"))
        }
        throwIfInvalid(violations)
        return requireNotNull(embeddingProfileId)
    }

    /**
     * delete ticket request는 authenticated principal 이외의 owner selector나 local path를 받지 않는다.
     * documentId만 closed JSON shape로 검증해 DB의 owner-and-document-bound capability와 연결한다.
     */
    fun parseV2DeleteTicketRequest(body: String): String {
        val root = parseObject(body)
        val violations = mutableListOf<RagFieldViolation>()
        rejectUnknown(root, V2_DELETE_TICKET_FIELDS, violations)
        requireExactString(
            root = root,
            field = "contractId",
            expected = "s4-rag-v2-delete-ticket-request-v1",
            violations = violations,
        )
        requireSchemaVersionOne(root, violations)
        requireExactString(
            root = root,
            field = "sourceScope",
            expected = "OWNER_PRIVATE",
            violations = violations,
        )
        val documentId = requiredString(root, "documentId", violations)
        if (documentId != null && !DOCUMENT_ID.matches(documentId)) {
            violations.add(RagFieldViolation("/documentId", "INVALID_FORMAT"))
        }
        throwIfInvalid(violations)
        return requireNotNull(documentId)
    }

    /**
     * Vertex activation은 body selector가 아니라 preparation에서 발급한 opaque scope header만 재사용한다.
     * duplicate/invalid header는 same-request DB scope 또는 provider socket을 만들기 전에 reject한다.
     */
    fun parseV2VertexScopeClaim(request: HttpServletRequest): String? {
        val values = Collections.list(request.getHeaders(VERTEX_SCOPE_HEADER))
        if (values.isEmpty()) {
            return null
        }
        if (values.size != 1 || !VERTEX_SCOPE_CLAIM.matches(values.single())) {
            throw RagValidationException(
                listOf(RagFieldViolation("/headers/$VERTEX_SCOPE_HEADER", "INVALID_FORMAT")),
            )
        }
        return values.single()
    }

    /**
     * Vertex packet은 preparation과 ask 사이에 immutable scope를 재사용하므로, history/ledger와 같은
     * `req_` request ID만 허용한다. 일반 RAG retrieval의 request ID surface를 넓히지 않는다.
     */
    fun requireV2VertexRequestId(value: String): String {
        if (!VERTEX_REQUEST_ID.matches(value)) {
            throw RagValidationException(
                listOf(RagFieldViolation("/headers/X-Request-Id", "INVALID_FORMAT")),
            )
        }
        return value
    }

    fun parseHistoryQuery(request: HttpServletRequest): RagHistoryQuery {
        val violations =
            request.parameterMap.keys
                .filterNot { it in HISTORY_QUERY_FIELDS }
                .map { name ->
                    RagFieldViolation(
                        "/query/${escapePointer(name)}",
                        "UNKNOWN_FIELD",
                    )
                }.toMutableList()
        val cursor =
            request.parameterMap["cursor"]?.let { values ->
                if (values.size != 1 || values.single().isBlank() || values.single().length > 512) {
                    violations.add(RagFieldViolation("/query/cursor", "INVALID_CURSOR"))
                    null
                } else {
                    values.single()
                }
            }
        val limit =
            request.parameterMap["limit"]?.let { values ->
                if (values.size != 1) {
                    violations.add(RagFieldViolation("/query/limit", "INVALID_FORMAT"))
                    null
                } else {
                    values.single().toIntOrNull()?.takeIf { it in 1..50 }
                        ?: run {
                            violations.add(RagFieldViolation("/query/limit", "OUT_OF_RANGE"))
                            null
                        }
                }
            } ?: 20
        throwIfInvalid(violations)
        return RagHistoryQuery(cursor = cursor, limit = limit)
    }

    /**
     * `/rag/sources`는 cursor/query/filter CRUD 표면을 열지 않는 고정 metadata 목록이다.
     * 검색어, sourceTier, profile 같은 제어 입력은 S4.3 이후 별도 계약으로만 추가한다.
     */
    fun requireNoQuery(request: HttpServletRequest) {
        val violations =
            request.parameterMap.keys
                .sorted()
                .take(MAX_QUERY_VIOLATIONS)
                .map { name -> RagFieldViolation(boundedQueryPointer(name), "UNKNOWN_FIELD") }
        if (violations.isNotEmpty()) {
            throw RagValidationException(violations)
        }
    }

    private fun parseObject(body: String): JsonNode {
        val root =
            try {
                strictMapper.readTree(body)
            } catch (_: JacksonException) {
                null
            } catch (_: IllegalArgumentException) {
                null
            }
        if (root == null || !root.isObject) {
            throw RagValidationException(listOf(RagFieldViolation("/", "INVALID_FORMAT")))
        }
        return root
    }

    private fun rejectUnknown(
        root: JsonNode,
        allowed: Set<String>,
        violations: MutableList<RagFieldViolation>,
    ) {
        root.properties().forEach { (name, _) ->
            if (name !in allowed) {
                violations.add(RagFieldViolation("/${escapePointer(name)}", "UNKNOWN_FIELD"))
            }
        }
    }

    private fun requiredString(
        root: JsonNode,
        field: String,
        violations: MutableList<RagFieldViolation>,
    ): String? {
        val node = root.get(field)
        if (node == null) {
            violations.add(RagFieldViolation("/$field", "REQUIRED"))
            return null
        }
        if (!node.isString || node.stringValue().isBlank()) {
            violations.add(RagFieldViolation("/$field", "INVALID_FORMAT"))
            return null
        }
        return node.stringValue()
    }

    private fun requireExactString(
        root: JsonNode,
        field: String,
        expected: String,
        violations: MutableList<RagFieldViolation>,
    ) {
        val value = requiredString(root, field, violations)
        if (value != null && value != expected) {
            violations.add(RagFieldViolation("/$field", "INVALID_ENUM"))
        }
    }

    private fun requireSchemaVersionOne(
        root: JsonNode,
        violations: MutableList<RagFieldViolation>,
    ) = requireSchemaVersion(root, expected = 1, violations)

    private fun requireSchemaVersion(
        root: JsonNode,
        expected: Int,
        violations: MutableList<RagFieldViolation>,
    ) {
        val schemaVersion = root.get("schemaVersion")
        if (schemaVersion == null) {
            violations.add(RagFieldViolation("/schemaVersion", "REQUIRED"))
        } else if (!schemaVersion.isInt || schemaVersion.intValue() != expected) {
            violations.add(RagFieldViolation("/schemaVersion", "INVALID_FORMAT"))
        }
    }

    private fun requiredHexDigest(
        root: JsonNode,
        field: String,
        violations: MutableList<RagFieldViolation>,
    ): String? {
        val value = requiredString(root, field, violations)
        if (value != null && !HEX_DIGEST.matches(value)) {
            violations.add(RagFieldViolation("/$field", "INVALID_FORMAT"))
        }
        return value
    }

    private fun stringArray(
        root: JsonNode,
        field: String,
        allowed: Set<String>?,
        pattern: Regex?,
        violations: MutableList<RagFieldViolation>,
    ): List<String> {
        val node = root.get(field) ?: return emptyList()
        if (!node.isArray || node.size() > 5) {
            violations.add(RagFieldViolation("/$field", "OUT_OF_RANGE"))
            return emptyList()
        }
        val values =
            node
                .values()
                .asSequence()
                .mapIndexedNotNull { index, value ->
                    if (!value.isString) {
                        violations.add(RagFieldViolation("/$field/$index", "INVALID_FORMAT"))
                        null
                    } else {
                        value.stringValue()
                    }
                }.toList()
        if (values.size != values.toSet().size) {
            violations.add(RagFieldViolation("/$field", "DUPLICATE"))
        }
        values.forEachIndexed { index, value ->
            if ((allowed != null && value !in allowed) || (pattern != null && !pattern.matches(value))) {
                violations.add(RagFieldViolation("/$field/$index", "INVALID_FORMAT"))
            }
        }
        return values
    }

    private fun throwIfInvalid(violations: List<RagFieldViolation>) {
        if (violations.isNotEmpty()) {
            throw RagValidationException(violations.take(MAX_QUERY_VIOLATIONS))
        }
    }

    private fun escapePointer(value: String): String =
        value
            .replace("~", "~0")
            .replace("/", "~1")

    private fun boundedQueryPointer(name: String): String {
        val pointer = "/query/${escapePointer(name)}"
        if (pointer.length <= MAX_FIELD_LENGTH) {
            return pointer
        }
        // 긴 attacker-controlled 이름은 절단 충돌 대신 고정 SHA-256 sentinel로 응답 schema 상한에 맞춘다.
        val digest =
            HexFormat
                .of()
                .formatHex(MessageDigest.getInstance("SHA-256").digest(name.toByteArray(StandardCharsets.UTF_8)))
        return "/query/__name_sha256_$digest"
    }

    private companion object {
        const val MAX_QUERY_VIOLATIONS = 64
        const val MAX_FIELD_LENGTH = 256
        const val MAX_ASK_BYTES = 32_768
        const val MAX_QUESTION_BYTES = 8_192
        val ASK_FIELDS = setOf("question", "answerMode", "relatedSymbols", "topics")
        val CONSENT_FIELDS = setOf("consentType", "action", "policyVersion")
        val V2_EXTERNAL_CONSENT_FIELDS =
            setOf(
                "contractId",
                "schemaVersion",
                "consentType",
                "action",
                "disclosureDigest",
                "policyDigest",
                "processorSetDigest",
            )
        val V2_IMPORT_TICKET_FIELDS =
            setOf("contractId", "schemaVersion", "sourceScope", "importMode", "embeddingProfileId")
        val OWNER_EMBEDDING_PROFILES = setOf("bge_m3_local_1024_v1", "voyage_context_4_1024_v1")
        val V2_DELETE_TICKET_FIELDS = setOf("contractId", "schemaVersion", "sourceScope", "documentId")
        const val VERTEX_SCOPE_HEADER = "X-Rag-V2-Vertex-Scope-Claim"
        val HISTORY_QUERY_FIELDS = setOf("cursor", "limit")
        val HEX_DIGEST = Regex("^[0-9a-f]{64}$")
        val SYMBOL = Regex("^[0-9]{6}$")
        val ANSWER_ID = Regex("^rag_ans_[0-9a-f]{32}$")
        val V2_ANSWER_ID = Regex("^rag_[A-Za-z0-9_-]{12,96}$")
        val DOCUMENT_ID = Regex("^doc_[a-z0-9][a-z0-9_-]{10,95}$")
        val VERTEX_SCOPE_CLAIM = Regex("^rvs_[0-9a-f]{32}$")
        val VERTEX_REQUEST_ID = Regex("^req_[A-Za-z0-9_-]{12,96}$")
        val IDEMPOTENCY_KEY = Regex("^[A-Za-z0-9._~-]{16,128}$")
        val TOPICS =
            setOf(
                "API",
                "DATA",
                "FINANCIAL_ENGINEERING",
                "METHODOLOGY",
                "PRODUCT_RISK",
                "RISK",
            )
    }
}
