package com.capstone.decision.infrastructure.rag

import org.springframework.core.io.ClassPathResource
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.JsonNode
import tools.jackson.databind.json.JsonMapper
import java.net.URI
import java.security.MessageDigest
import java.text.Normalizer
import java.time.Instant

data class ValidatedRagSourceCardV2(
    val schemaVersion: String,
    val cardVariant: String,
    val sourceId: String,
    val cardId: String,
)

object RagSourceCardV2Contract {
    private val strictMapper =
        JsonMapper
            .builder(
                JsonFactory
                    .builder()
                    .streamReadConstraints(
                        StreamReadConstraints
                            .builder()
                            .maxDocumentLength(MAX_CARD_BYTES)
                            .maxNestingDepth(12)
                            .maxNameLength(128)
                            .maxStringLength(4096)
                            .maxTokenCount(20_000)
                            .build(),
                    ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                    .build(),
            ).build()
    private val schemaAttested: Boolean by lazy { attestSchemaResource() }

    /**
     * Python과 같은 source-card v2 discriminator·lineage·외부처리 경계를 검증한다.
     * 이 경계는 raw evidence를 읽거나 provider 호출을 만들지 않고 bounded JSON bytes만 소비한다.
     */
    fun validate(payload: ByteArray): ValidatedRagSourceCardV2 {
        try {
            check(schemaAttested)
            require(payload.isNotEmpty() && payload.size <= MAX_CARD_BYTES) {
                "RAG source card v2 payload size is invalid."
            }
            val root = strictMapper.readTree(payload)
            validateRoot(root)
            return ValidatedRagSourceCardV2(
                schemaVersion = requiredText(root, "schemaVersion"),
                cardVariant = requiredText(root, "cardVariant"),
                sourceId = requiredText(root, "sourceId"),
                cardId = requiredText(root, "cardId"),
            )
        } catch (error: IllegalArgumentException) {
            if (error.message?.contains("source card v2") == true) {
                throw error
            }
            throw IllegalArgumentException(
                "RAG source card v2 validation failed.",
                error,
            )
        } catch (error: Exception) {
            throw IllegalArgumentException(
                "RAG source card v2 validation failed.",
                error,
            )
        }
    }

    private fun attestSchemaResource(): Boolean {
        val schemaBytes =
            ClassPathResource(SCHEMA_RESOURCE)
                .inputStream
                .use { it.readBytes() }
        val schema = strictMapper.readTree(schemaBytes)
        require(requiredText(schema, "\$id") == SCHEMA_ID) {
            "RAG source card v2 schema ID drifted."
        }
        require(schema.path("type").stringValue() == "object")
        require(
            schema.path("additionalProperties").isBoolean &&
                !schema.path("additionalProperties").booleanValue(),
        )
        require(schema.path("oneOf").isArray && schema.path("oneOf").size() == 2)
        require(textArray(schema.path("required")).toSet() == COMMON_FIELDS)
        require(
            schema
                .path("properties")
                .propertyNames()
                .asSequence()
                .toSet() == SCHOLARLY_FIELDS,
        )
        return true
    }

    private fun validateRoot(root: JsonNode) {
        require(root.isObject) {
            "RAG source card v2 payload must be an object."
        }
        val variant = requiredText(root, "cardVariant")
        val expectedFields =
            when (variant) {
                OFFICIAL_VARIANT -> COMMON_FIELDS
                SCHOLARLY_VARIANT -> SCHOLARLY_FIELDS
                else -> throw IllegalArgumentException(
                    "RAG source card v2 discriminator is invalid.",
                )
            }
        require(root.propertyNames().asSequence().toSet() == expectedFields) {
            "RAG source card v2 fields drifted."
        }
        root.walkText().forEach(::validateText)
        require(requiredText(root, "schemaVersion") == "2")
        require(requiredText(root, "sourceType") == "PROJECT_SOURCE_CARD")
        require(requiredText(root, "tier") == "PROJECT")
        require(requiredText(root, "retentionOwner") == "python-rag-corpus-privacy")
        require(requiredText(root, "adoptedSession") in setOf("S4.7A", "S4.7B"))
        require(requiredText(root, "status") in setOf("VERIFIED", "BLOCKED_EVIDENCE", "RETIRED"))
        require(requiredText(root, "accessLevel") in setOf("PUBLIC", "INTERNAL"))
        require(requiredText(root, "claim").length in 20..1000)
        require(requiredText(root, "accessNote").length in 8..1000)
        require(requiredText(root, "licenseNote").length in 8..1000)
        require(requiredText(root, "attribution").length in 2..500)
        require(root.path("retentionDays").isIntegralNumber)
        require(root.path("retentionDays").intValue() in 1..3650)
        validateIdentity(root)
        validateVerifiedAt(root)
        validateUrlAndDigest(root)
        validateRequiredTextArrays(root)
        validateVariant(root, variant)
        validateExternalProcessing(root)
        validateModelAssumptions(root)
    }

    private fun validateIdentity(root: JsonNode) {
        val sourceId = requiredText(root, "sourceId")
        val cardId = requiredText(root, "cardId")
        val topic = requiredText(root, "topic")
        val sourceMatch = SOURCE_ID_PATTERN.matchEntire(sourceId)
        require(sourceMatch != null && sourceMatch.groups["topic"]?.value == topic) {
            "RAG source card v2 sourceId must encode the exact topic."
        }
        require(cardId == "card_${topic}_${sourceMatch.groups["sequence"]?.value}") {
            "RAG source card v2 cardId must match source topic and sequence."
        }
        require(cardId !in textArray(root.path("contradicts"))) {
            "RAG source card v2 cannot contradict itself."
        }
    }

    private fun validateVerifiedAt(root: JsonNode) {
        val verifiedAt = requiredText(root, "verifiedAt")
        require(UTC_PATTERN.matches(verifiedAt)) {
            "RAG source card v2 verifiedAt must use canonical UTC Z."
        }
        try {
            Instant.parse(verifiedAt)
        } catch (error: Exception) {
            throw IllegalArgumentException(
                "RAG source card v2 verifiedAt is invalid.",
                error,
            )
        }
    }

    private fun validateUrlAndDigest(root: JsonNode) {
        val url = requiredText(root, "canonicalUrl")
        val uri =
            try {
                URI(url)
            } catch (error: Exception) {
                throw IllegalArgumentException(
                    "RAG source card v2 canonical URL is invalid.",
                    error,
                )
            }
        val host = uri.host
        require(
            uri.scheme == "https" &&
                !host.isNullOrBlank() &&
                host == host.lowercase() &&
                HOST_PATTERN.matches(host) &&
                "." in host &&
                host != "localhost" &&
                !host.endsWith(".localhost") &&
                !IPV4_PATTERN.matches(host) &&
                uri.rawUserInfo == null &&
                uri.rawFragment == null &&
                uri.port in setOf(-1, 443) &&
                !uri.rawPath.isNullOrBlank() &&
                !uri.rawPath.startsWith("//") &&
                "//" !in uri.rawPath &&
                "\\" !in url &&
                "%" !in url &&
                uri.rawPath.split("/").none { it == "." || it == ".." },
        ) {
            "RAG source card v2 canonical URL is unsafe."
        }
        val queryKeys =
            uri.rawQuery
                ?.split("&")
                ?.filter(String::isNotBlank)
                ?.map { pair -> pair.substringBefore("=").lowercase() }
                .orEmpty()
        require(queryKeys.none(REDIRECT_QUERY_KEYS::contains)) {
            "RAG source card v2 redirect URL is forbidden."
        }
        require(requiredText(root, "canonicalUrlSha256") == sha256(url.toByteArray())) {
            "RAG source card v2 canonical URL digest mismatched."
        }
    }

    private fun validateRequiredTextArrays(root: JsonNode) {
        REQUIRED_NON_EMPTY_ARRAYS.forEach { field ->
            val values = textArray(root.path(field))
            require(values.isNotEmpty() && values.size <= 12) {
                "RAG source card v2 $field bounds drifted."
            }
            require(values.toSet().size == values.size) {
                "RAG source card v2 $field values must be unique."
            }
        }
        require(textArray(root.path("representativeQuestions")).size in 1..5)
        require(textArray(root.path("contradicts")).size <= 10)
    }

    private fun validateVariant(
        root: JsonNode,
        variant: String,
    ) {
        val institution = requiredText(root, "institution")
        val evidenceClass = requiredText(root, "evidenceClass")
        val upstreamIds = textArray(root.path("upstreamSourceIds"))
        when (variant) {
            OFFICIAL_VARIANT -> {
                require(upstreamIds.size in 1..5 && upstreamIds.toSet().size == upstreamIds.size)
                require(upstreamIds.all(UPSTREAM_SOURCE_IDS::contains))
                require(upstreamIds.any { it.startsWith("src_${institution}_") }) {
                    "RAG source card v2 official authority mismatched upstream lineage."
                }
                require(institution in OFFICIAL_AUTHORITY_INSTITUTIONS[evidenceClass].orEmpty()) {
                    "RAG source card v2 official evidence authority mismatched."
                }
            }

            SCHOLARLY_VARIANT -> {
                require(upstreamIds.isEmpty()) {
                    "RAG source card v2 scholarly lineage cannot use upstream IDs."
                }
                require(evidenceClass in SCHOLARLY_EVIDENCE_CLASSES)
                validateBibliography(root)
            }
        }
    }

    private fun validateBibliography(root: JsonNode) {
        val locator = root.path("bibliographicLocator")
        val metadata = root.path("bibliographicMetadata")
        require(locator.isObject && locator.propertyNames().asSequence().toSet() == LOCATOR_FIELDS)
        require(metadata.isObject && metadata.propertyNames().asSequence().toSet() == METADATA_FIELDS)
        val canonicalUrl = requiredText(root, "canonicalUrl")
        val hostname = URI(canonicalUrl).host
        require(SECONDARY_HOSTS.none { hostname == it || hostname.endsWith(".$it") }) {
            "RAG source card v2 secondary blog cannot be primary evidence."
        }
        val locatorType = requiredText(locator, "locatorType")
        val authorityType = requiredText(locator, "authorityType")
        val value = requiredText(locator, "value")
        when (locatorType) {
            "DOI" ->
                require(
                    authorityType == "DOI_REGISTRY" &&
                        DOI_PATTERN.matches(value) &&
                        canonicalUrl == "https://doi.org/$value",
                ) {
                    "RAG source card v2 DOI locator is invalid."
                }

            "ISBN" ->
                require(
                    authorityType in
                        setOf(
                            "ISBN_REGISTRY",
                            "OFFICIAL_PUBLISHER",
                            "OFFICIAL_INSTITUTION",
                        ) &&
                        ISBN_PATTERN.matches(value.replace("-", "").replace(" ", "")),
                ) {
                    "RAG source card v2 ISBN locator is invalid."
                }

            "OFFICIAL_URL" ->
                require(
                    authorityType in
                        setOf(
                            "OFFICIAL_PUBLISHER",
                            "OFFICIAL_AUTHOR_ARCHIVE",
                            "OFFICIAL_INSTITUTION",
                        ) &&
                        value == canonicalUrl,
                ) {
                    "RAG source card v2 official locator is invalid."
                }

            else -> throw IllegalArgumentException(
                "RAG source card v2 locator type is invalid.",
            )
        }
        require(textArray(metadata.path("authors")).isNotEmpty())
        require(requiredText(metadata, "title").length in 2..500)
        require(requiredText(metadata, "venue").length in 2..300)
        require(requiredText(metadata, "editionOrVersion").length in 1..300)
        require(metadata.path("year").isIntegralNumber)
        require(metadata.path("year").intValue() in 1600..2100)
    }

    private fun validateExternalProcessing(root: JsonNode) {
        val externalAllowed = requiredBoolean(root, "externalProcessingAllowed")
        val contentClass = requiredText(root, "contentClass")
        val gate = requiredText(root, "externalProcessingGate")
        require(
            contentClass in
                setOf(
                    "RAW_OR_REFERENCE_EVIDENCE",
                    "PROJECT_AUTHORED_SANITIZED_CARD",
                ),
        )
        if (externalAllowed) {
            require(
                contentClass == "PROJECT_AUTHORED_SANITIZED_CARD" &&
                    gate == "LICENSE_AND_CONSENT_VERIFIED",
            ) {
                "RAG source card v2 external processing lacks its explicit gate."
            }
        } else {
            require(gate == "NOT_GRANTED") {
                "RAG source card v2 disabled external processing gate drifted."
            }
        }
        require(contentClass != "RAW_OR_REFERENCE_EVIDENCE" || !externalAllowed) {
            "RAG source card v2 raw/reference evidence cannot leave the boundary."
        }
    }

    private fun validateModelAssumptions(root: JsonNode) {
        val assumptions = root.path("modelAssumptions")
        require(assumptions.isArray && assumptions.size() <= 12)
        val keys =
            assumptions
                .values()
                .asSequence()
                .map { assumption ->
                    require(
                        assumption.isObject &&
                            assumption.propertyNames().asSequence().toSet() == ASSUMPTION_FIELDS,
                    )
                    val key = requiredText(assumption, "key")
                    require(ASSUMPTION_KEY_PATTERN.matches(key))
                    require(requiredText(assumption, "statement").length in 12..1000)
                    key
                }.toList()
        require(keys.toSet().size == keys.size) {
            "RAG source card v2 model assumption keys must be unique."
        }
        require(requiredBoolean(root, "modelSensitive") == keys.isNotEmpty()) {
            "RAG source card v2 modelSensitive and assumptions disagree."
        }
    }

    private fun validateText(text: String) {
        require(Normalizer.isNormalized(text, Normalizer.Form.NFC)) {
            "RAG source card v2 text must be NFC-normalized."
        }
        require(
            text.none { character ->
                character.code < 0x20 ||
                    character.code == 0x7F ||
                    Character.isSurrogate(character)
            },
        ) {
            "RAG source card v2 text contains control or surrogate characters."
        }
        require(!INSTRUCTION_LIKE_PATTERN.containsMatchIn(text)) {
            "RAG source card v2 contains instruction-like control text."
        }
        require(!PRIVATE_PATH_PATTERN.containsMatchIn(text)) {
            "RAG source card v2 contains a private filesystem locator."
        }
    }

    private fun requiredText(
        node: JsonNode,
        field: String,
    ): String {
        val value = node.path(field)
        require(value.isString && value.stringValue().isNotBlank()) {
            "RAG source card v2 $field must be non-empty text."
        }
        return value.stringValue()
    }

    private fun requiredBoolean(
        node: JsonNode,
        field: String,
    ): Boolean {
        val value = node.path(field)
        require(value.isBoolean) {
            "RAG source card v2 $field must be boolean."
        }
        return value.booleanValue()
    }

    private fun textArray(node: JsonNode): List<String> {
        require(
            node.isArray &&
                node.values().asSequence().all { it.isString && it.stringValue().isNotBlank() },
        ) {
            "RAG source card v2 text array is invalid."
        }
        return node
            .values()
            .asSequence()
            .map(JsonNode::stringValue)
            .toList()
    }

    private fun JsonNode.walkText(): Sequence<String> =
        sequence {
            when {
                isString -> yield(stringValue())
                isArray ->
                    for (child in values().asSequence()) {
                        yieldAll(child.walkText())
                    }
                isObject ->
                    for (child in values().asSequence()) {
                        yieldAll(child.walkText())
                    }
            }
        }

    private fun sha256(bytes: ByteArray): String =
        MessageDigest
            .getInstance("SHA-256")
            .digest(bytes)
            .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }

    private const val MAX_CARD_BYTES = 32_768L
    private const val SCHEMA_RESOURCE = "contracts/rag-source-card-v2.schema.json"
    private const val SCHEMA_ID = "contracts/schemas/rag-source-card-v2.schema.json"
    private const val OFFICIAL_VARIANT = "OFFICIAL_UPSTREAM_CARD"
    private const val SCHOLARLY_VARIANT = "SCHOLARLY_PRIMARY_CARD"
    private val COMMON_FIELDS =
        setOf(
            "schemaVersion",
            "cardVariant",
            "sourceId",
            "cardId",
            "title",
            "institution",
            "topic",
            "sourceType",
            "tier",
            "accessLevel",
            "claim",
            "evidenceClass",
            "status",
            "verifiedAt",
            "accessNote",
            "licenseNote",
            "attribution",
            "canonicalUrl",
            "canonicalUrlSha256",
            "evidenceContentSha256",
            "upstreamSourceIds",
            "retentionOwner",
            "retentionDays",
            "contentClass",
            "externalProcessingAllowed",
            "externalProcessingGate",
            "adoptedSession",
            "contradicts",
            "modelSensitive",
            "modelAssumptions",
            "limitations",
            "allowedUses",
            "forbiddenInferences",
            "representativeQuestions",
        )
    private val SCHOLARLY_FIELDS = COMMON_FIELDS + setOf("bibliographicLocator", "bibliographicMetadata")
    private val LOCATOR_FIELDS = setOf("authorityType", "locatorType", "value")
    private val METADATA_FIELDS = setOf("authors", "editionOrVersion", "title", "venue", "year")
    private val ASSUMPTION_FIELDS = setOf("key", "statement")
    private val REQUIRED_NON_EMPTY_ARRAYS = setOf("allowedUses", "limitations", "forbiddenInferences")
    private val OFFICIAL_AUTHORITY_INSTITUTIONS =
        mapOf(
            "OFFICIAL_API_DOCUMENTATION" to setOf("ecos", "kis", "opendart"),
            "OFFICIAL_SERVICE_DOCUMENTATION" to setOf("krx"),
            "OFFICIAL_PRODUCT_DOCUMENTATION" to setOf("samsungfund"),
        )
    private val SCHOLARLY_EVIDENCE_CLASSES =
        setOf("PRIMARY_RESEARCH", "OFFICIAL_REPORT", "OFFICIAL_STANDARD")
    private val UPSTREAM_SOURCE_IDS =
        setOf(
            "src_kis_openapi_overview_001",
            "src_kis_marketdata_daily_001",
            "src_kis_marketdata_price_001",
            "src_kis_trading_cash_order_001",
            "src_kis_account_balance_001",
            "src_kis_market_calendar_001",
            "src_kis_rate_limit_001",
            "src_opendart_disclosure_search_001",
            "src_opendart_corporation_code_001",
            "src_opendart_financial_statement_001",
            "src_opendart_major_report_001",
            "src_ecos_api_overview_001",
            "src_ecos_statistic_search_001",
            "src_krx_openapi_service_catalog_001",
            "src_krx_openapi_terms_001",
            "src_krx_etf_etn_structure_001",
            "src_krx_etn_risk_indicator_001",
            "src_samsungfund_gold_futures_etf_001",
            "src_naver_news_search_001",
            "src_naver_legacy_sunset_001",
        )
    private val SECONDARY_HOSTS =
        setOf("blogspot.com", "medium.com", "substack.com", "wikipedia.org", "wordpress.com")
    private val REDIRECT_QUERY_KEYS =
        setOf(
            "continue",
            "dest",
            "destination",
            "next",
            "redirect",
            "redirect_uri",
            "return",
            "return_to",
            "target",
            "url",
        )
    private val SOURCE_ID_PATTERN =
        Regex("""src_project_(?<topic>[a-z0-9][a-z0-9_]*?)_(?<sequence>[0-9]{3})""")
    private val UTC_PATTERN =
        Regex("""[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z""")
    private val HOST_PATTERN = Regex("""[a-z0-9.-]+""")
    private val IPV4_PATTERN = Regex("""[0-9]{1,3}(?:\.[0-9]{1,3}){3}""")
    private val DOI_PATTERN = Regex("""10\.[0-9]{4,9}/[-._;()/:A-Za-z0-9]+""")
    private val ISBN_PATTERN = Regex("""(?:[0-9]{9}[0-9X]|[0-9]{13})""")
    private val ASSUMPTION_KEY_PATTERN = Regex("""[A-Z][A-Z0-9_]{2,127}""")
    private val INSTRUCTION_LIKE_PATTERN =
        Regex(
            """(?i)(ignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior)\s+instructions""" +
                """|system\s+prompt""" +
                """|(?:reveal|print|exfiltrate)\b.{0,40}\b(?:secret|token|credential)s?\b""" +
                """|(?:execute|run)\b.{0,30}\b(?:shell|command|code)\b""" +
                """|(?:call|invoke)\b.{0,30}\b(?:tool|mcp|plugin)\b""" +
                """|(?:place|submit|cancel)\b.{0,30}\border\b""" +
                """|(?:이전|기존)\s*지시.{0,12}무시""" +
                """|시스템\s*프롬프트""" +
                """|비밀.{0,20}(?:출력|노출)""" +
                """|도구.{0,20}(?:호출|실행))""",
        )
    private val PRIVATE_PATH_PATTERN =
        Regex(
            """(?i)(?:^|[\s"'])(?:(?:/home|/Users|/mnt/[a-z]|[A-Z]:[\\/]""" +
                """|\\\\wsl(?:\.localhost)?\\)[^\s"']*|file:(?://)?[^\s"']*)""",
        )
}
