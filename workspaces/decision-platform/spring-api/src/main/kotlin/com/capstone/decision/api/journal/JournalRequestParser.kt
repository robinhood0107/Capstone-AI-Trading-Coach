package com.capstone.decision.api.journal

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.application.journal.CreateJournalCommand
import com.capstone.decision.application.journal.DeleteJournalCommand
import com.capstone.decision.application.journal.JournalLinks
import com.capstone.decision.application.journal.ReplaceJournalCommand
import com.capstone.decision.application.security.IdempotencyKeyPolicy
import jakarta.servlet.http.HttpServletRequest
import org.springframework.stereotype.Component
import tools.jackson.core.JacksonException
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.JsonNode
import tools.jackson.databind.json.JsonMapper
import java.text.Normalizer

data class JournalListQuery(
    val size: Int,
    val cursor: String?,
)

/** Journal content를 bounded raw String에서 strict closed command로 바꾸며 owner 값은 받지 않는다. */
@Component
class JournalRequestParser {
    private val strictMapper =
        JsonMapper
            .builder(
                JsonFactory
                    .builder()
                    .streamReadConstraints(
                        StreamReadConstraints
                            .builder()
                            .maxNestingDepth(5)
                            .maxDocumentLength(MAX_DOCUMENT_BYTES)
                            .maxTokenCount(96)
                            .maxStringLength(8192)
                            .maxNameLength(64)
                            .build(),
                    ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                    .build(),
            ).build()

    fun parseCreate(body: String): CreateJournalCommand {
        val root = parseObject(body, CREATE_FIELDS)
        return CreateJournalCommand(
            title = canonicalText(root, "title", 120, multiline = false),
            content = canonicalText(root, "content", 8192, multiline = true),
            tags = tags(root),
            links = links(root),
        )
    }

    fun parseReplace(body: String): ReplaceJournalCommand {
        val root = parseObject(body, REPLACE_FIELDS)
        return ReplaceJournalCommand(
            expectedVersion = version(root),
            title = canonicalText(root, "title", 120, multiline = false),
            content = canonicalText(root, "content", 8192, multiline = true),
            tags = tags(root),
            links = links(root),
        )
    }

    fun parseDelete(body: String): DeleteJournalCommand {
        val root = parseObject(body, DELETE_FIELDS)
        return DeleteJournalCommand(version(root))
    }

    fun parseJournalId(value: String): String = value.takeIf(JOURNAL_ID::matches) ?: invalid("/path/journalId")

    fun parseListQuery(request: HttpServletRequest): JournalListQuery {
        rejectUnknownOrRepeatedQuery(request, LIST_QUERY_FIELDS)
        val rawSize = request.getParameter("size")
        val size = rawSize?.toIntOrNull() ?: if (rawSize == null) DEFAULT_PAGE_SIZE else invalid("/query/size")
        if (size !in 1..MAX_PAGE_SIZE) invalid("/query/size")
        val cursor = request.getParameter("cursor")
        if (cursor != null && (cursor.length !in 1..MAX_CURSOR_CHARS || !BASE64_URL.matches(cursor))) {
            invalid("/query/cursor")
        }
        return JournalListQuery(size, cursor)
    }

    fun requireNoQuery(request: HttpServletRequest) {
        rejectUnknownOrRepeatedQuery(request, emptySet())
    }

    fun requireIdempotencyKey(value: String?): String =
        value?.takeIf(IdempotencyKeyPolicy::isValid)
            ?: invalid("/headers/X-Idempotency-Key")

    private fun parseObject(
        body: String,
        allowedFields: Set<String>,
    ): JsonNode {
        if (body.toByteArray(Charsets.UTF_8).size !in 2..MAX_DOCUMENT_BYTES.toInt()) invalid("/")
        val root =
            try {
                strictMapper.readTree(body)
            } catch (_: JacksonException) {
                null
            } catch (_: IllegalArgumentException) {
                null
            }
        if (root == null || !root.isObject) invalid("/")
        root.properties().forEach { (name, _) -> if (name !in allowedFields) invalid("/${escape(name)}") }
        return root
    }

    private fun canonicalText(
        root: JsonNode,
        field: String,
        maximum: Int,
        multiline: Boolean,
    ): String {
        val node = root.get(field)
        if (node == null || !node.isString) invalid("/$field")
        val value = node.stringValue()
        val codePoints = value.codePointCount(0, value.length)
        val invalidControl =
            value.codePoints().anyMatch { codePoint ->
                val allowedWhitespace = multiline && (codePoint == '\n'.code || codePoint == '\t'.code)
                !allowedWhitespace && (Character.isISOControl(codePoint) || Character.getType(codePoint) == Character.FORMAT.toInt())
            }
        if (
            value != value.trim() ||
            Normalizer.normalize(value, Normalizer.Form.NFC) != value ||
            codePoints !in 1..maximum ||
            invalidControl
        ) {
            invalid("/$field")
        }
        return value
    }

    private fun tags(root: JsonNode): List<String> {
        val node = root.get("tags")
        if (node == null || !node.isArray || node.size() > 20) invalid("/tags")
        val values =
            node.mapIndexed { index, item ->
                if (!item.isString) invalid("/tags/$index")
                val wrapper = strictMapper.createObjectNode().put("tag", item.stringValue())
                canonicalText(wrapper, "tag", 32, multiline = false)
            }
        if (values.size != values.toSet().size) invalid("/tags")
        return values
    }

    private fun links(root: JsonNode): JournalLinks {
        val node = root.get("links")
        if (node == null || !node.isObject) invalid("/links")
        node.properties().forEach { (name, _) -> if (name !in LINK_FIELDS) invalid("/links/${escape(name)}") }
        return JournalLinks(
            decisionId = optionalId(node, "decisionId", DECISION_ID),
            backtestRunId = optionalId(node, "backtestRunId", BACKTEST_ID),
            ragAnswerId = optionalId(node, "ragAnswerId", RAG_ID),
            orderId = optionalId(node, "orderId", ORDER_ID),
            automationRunId = optionalId(node, "automationRunId", AUTOMATION_RUN_ID),
        )
    }

    private fun optionalId(
        root: JsonNode,
        field: String,
        pattern: Regex,
    ): String? {
        val node = root.get(field) ?: return null
        if (node.isNull) return null
        if (!node.isString || !pattern.matches(node.stringValue())) invalid("/links/$field")
        return node.stringValue()
    }

    private fun version(root: JsonNode): Int {
        val node = root.get("expectedVersion")
        if (node == null || !node.isIntegralNumber || !node.canConvertToInt()) invalid("/expectedVersion")
        return node.intValue().takeIf { it >= 1 } ?: invalid("/expectedVersion")
    }

    private fun rejectUnknownOrRepeatedQuery(
        request: HttpServletRequest,
        allowed: Set<String>,
    ) {
        request.parameterMap.forEach { (name, values) ->
            if (name !in allowed || values.size != 1) invalid("/query/${escape(name)}")
        }
    }

    private fun invalid(field: String): Nothing =
        throw ApiException(
            ErrorCode.VALIDATION_ERROR,
            details = mapOf("violations" to listOf(mapOf("field" to field, "reason" to "INVALID_FORMAT"))),
        )

    private fun escape(value: String): String = value.take(256).replace("~", "~0").replace("/", "~1")

    private companion object {
        const val MAX_DOCUMENT_BYTES = 65536L
        const val DEFAULT_PAGE_SIZE = 20
        const val MAX_PAGE_SIZE = 100
        const val MAX_CURSOR_CHARS = 512
        val CREATE_FIELDS = setOf("title", "content", "tags", "links")
        val REPLACE_FIELDS = CREATE_FIELDS + "expectedVersion"
        val DELETE_FIELDS = setOf("expectedVersion")
        val LINK_FIELDS = setOf("decisionId", "backtestRunId", "ragAnswerId", "orderId", "automationRunId")
        val LIST_QUERY_FIELDS = setOf("size", "cursor")
        val JOURNAL_ID = Regex("^jnl_[A-Za-z0-9_-]{8,96}$")
        val DECISION_ID = Regex("^dec_[A-Za-z0-9_-]{8,96}$")
        val BACKTEST_ID = Regex("^run_[A-Za-z0-9_-]{8,96}$")
        val RAG_ID = Regex("^rag_[A-Za-z0-9_-]{8,96}$")
        val ORDER_ID = Regex("^ord_[A-Za-z0-9_-]{8,96}$")
        val AUTOMATION_RUN_ID = Regex("^auto_run_[A-Za-z0-9_-]{8,96}$")
        val BASE64_URL = Regex("^[A-Za-z0-9_-]+$")
    }
}
