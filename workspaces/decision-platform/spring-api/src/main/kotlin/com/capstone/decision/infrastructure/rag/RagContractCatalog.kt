package com.capstone.decision.infrastructure.rag

import org.springframework.core.io.ClassPathResource
import org.springframework.stereotype.Component
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.JsonNode
import tools.jackson.databind.json.JsonMapper
import java.security.MessageDigest

// RAG profile/policy는 DB나 요청 문자열이 아니라 contracts catalog의 exact bytes에서만 읽는다.
@Component
class RagContractCatalog private constructor(
    catalogBytes: ByteArray,
    digestManifestBytes: ByteArray,
) {
    final val catalogSha256: String
    final val profileIds: List<String>
    final val policyIds: List<String>
    final val generationStatuses: List<String>
    final val topicAllowlist: List<String>
    final val dimension: Int
    final val askForbiddenBodyFields: Set<String>

    constructor() : this(
        resourceBytes(CATALOG_RESOURCE),
        resourceBytes(CATALOG_SHA256_MANIFEST_RESOURCE),
    )

    init {
        val strictMapper =
            JsonMapper
                .builder(
                    JsonFactory
                        .builder()
                        .streamReadConstraints(
                            StreamReadConstraints
                                .builder()
                                .maxDocumentLength(MAX_CATALOG_BYTES)
                                .maxNestingDepth(12)
                                .maxNameLength(128)
                                .maxStringLength(2048)
                                .maxTokenCount(20_000)
                                .build(),
                        ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                        .build(),
                ).build()
        val digestManifest = strictMapper.readTree(digestManifestBytes)
        check(
            digestManifest.propertyNames().asSequence().toSet() ==
                setOf("catalogPath", "contractChangePath", "schemaVersion", "sha256"),
        )
        check(requiredText(digestManifest, "catalogPath") == "contracts/catalogs/s4-rag-contract.v1.json")
        check(
            requiredText(digestManifest, "contractChangePath") ==
                "contracts/changes/20260729-s4-rag-contract-catalog.md",
        )
        check(
            digestManifest.path("schemaVersion").isIntegralNumber &&
                digestManifest.path("schemaVersion").intValue() == 1,
        )
        catalogSha256 = requiredText(digestManifest, "sha256")
        check(catalogSha256.matches(Regex("""[0-9a-f]{64}""")))
        check(sha256(catalogBytes) == catalogSha256) {
            "S4 RAG canonical catalog digest mismatch."
        }
        val root = strictMapper.readTree(catalogBytes)
        check(root.propertyNames().asSequence().toSet() == APPROVED_ROOT_FIELDS)
        check(requiredText(root, "contractId") == "s4-rag-contract/v1")
        check(root.path("schemaVersion").isIntegralNumber && root.path("schemaVersion").intValue() == 1)
        check(root.path("dimension").isIntegralNumber)
        dimension = root.path("dimension").intValue()
        check(dimension == 1024)

        profileIds = textArray(root.path("profileIds"))
        policyIds = textArray(root.path("policyIds"))
        check(profileIds == APPROVED_PROFILE_IDS)
        check(policyIds == APPROVED_POLICY_IDS)
        check(textArray(root.path("forbiddenProfileIds")).toSet() == setOf(FORBIDDEN_PROFILE_ID))
        check(FORBIDDEN_PROFILE_ID !in profileIds)
        check(textArray(root.path("answerModes")) == listOf("CONCISE", "DETAILED"))
        generationStatuses = textArray(root.path("generationStatuses"))
        check(generationStatuses == APPROVED_GENERATION_STATUSES)
        topicAllowlist = textArray(root.path("topicAllowlist"))
        check(topicAllowlist == APPROVED_TOPIC_ALLOWLIST)
        check(textArray(root.path("embeddingOperations")) == listOf("DOCUMENT_EMBED", "QUERY_EMBED"))
        check(
            textArray(root.path("embeddingInputStrategies")) ==
                listOf(
                    "BGE_TRANSIENT_ADJACENT_CONTEXT_MAX_15",
                    "VOYAGE_CONTEXTUAL_DOCUMENT_BATCH_OVERLAP_0",
                ),
        )

        validateProfiles(root.path("profiles"))
        validatePolicies(root.path("policies"))
        validateChunking(root.path("canonicalChunking"))
        validateSourceMetadata(root.path("sourceMetadata"))
        val askRequest = root.path("askRequest")
        check(askRequest.isObject && askRequest.propertyNames().asSequence().toSet() == APPROVED_ASK_FIELDS)
        check(requiredText(askRequest, "normalization") == "NFC")
        check(requiredText(askRequest, "route") == "POST /api/v1/rag/ask")
        check(requiredText(askRequest, "idempotencyHeader") == "X-Idempotency-Key")
        check(requiredText(askRequest, "idempotencyKeyPattern") == "^[A-Za-z0-9._~-]{16,128}$")
        check(askRequest.path("minimumQuestionUnicodeScalars").intValue() == 1)
        check(askRequest.path("maximumQuestionUnicodeScalars").intValue() == 1000)
        check(askRequest.path("maximumQuestionUtf8Bytes").intValue() == 8192)
        check(askRequest.path("relatedSymbolsMaximumItems").intValue() == 5)
        check(requiredText(askRequest, "relatedSymbolsPattern") == "^[0-9]{6}$")
        check(askRequest.path("topicsMaximumItems").intValue() == 5)
        askForbiddenBodyFields = textArray(askRequest.path("forbiddenBodyFields")).toSet()
        check(REQUIRED_FORBIDDEN_ASK_FIELDS.all(askForbiddenBodyFields::contains)) {
            "S4 RAG public ask body must not expose profile, policy, provider, or topK controls."
        }
    }

    private fun validateProfiles(nodes: JsonNode) {
        check(nodes.isArray && nodes.size() == 2)
        val profiles =
            nodes
                .values()
                .asSequence()
                .associateBy { requiredText(it, "profileId") }
        check(profiles.keys.toList() == APPROVED_PROFILE_IDS)
        profiles.values.forEach { profile ->
            check(profile.propertyNames().asSequence().toSet() == APPROVED_PROFILE_FIELDS)
            check(profile.path("dimension").isIntegralNumber && profile.path("dimension").intValue() == 1024)
            check(requiredText(profile, "vectorSpace") == requiredText(profile, "profileId"))
            check(textArray(profile.path("operationAllowlist")) == listOf("DOCUMENT_EMBED", "QUERY_EMBED"))
            check(profile.path("trustRemoteCode").isBoolean && !profile.path("trustRemoteCode").booleanValue())
            check(
                profile.path("canonicalChunkOverlapPercent").isIntegralNumber &&
                    profile.path("canonicalChunkOverlapPercent").intValue() == 0,
            )
        }
        val bge = profiles.getValue("bge_m3_local_1024_v1")
        check(requiredText(bge, "provider") == "LOCAL")
        check(bge.path("externalProvider").isBoolean && !bge.path("externalProvider").booleanValue())
        check(bge.path("freeTokenEligible").isBoolean && !bge.path("freeTokenEligible").booleanValue())
        check(requiredText(bge, "artifactFormat") == "ONNX_DATA_ONLY")
        check(requiredText(bge, "model") == "BAAI/bge-m3")
        check(bge.path("providerOrigin").isNull && bge.path("providerEndpoint").isNull)
        check(requiredText(bge, "embeddingInputStrategy") == "BGE_TRANSIENT_ADJACENT_CONTEXT_MAX_15")
        check(bge.path("transientAdjacentContextMaxPercent").intValue() == 15)

        val voyage = profiles.getValue("voyage_context_4_1024_v1")
        check(requiredText(voyage, "provider") == "VOYAGE")
        check(requiredText(voyage, "model") == "voyage-context-4")
        check(voyage.path("externalProvider").isBoolean && voyage.path("externalProvider").booleanValue())
        check(voyage.path("freeTokenEligible").isBoolean && voyage.path("freeTokenEligible").booleanValue())
        check(requiredText(voyage, "artifactFormat") == "PROVIDER_API_RESPONSE_DATA_ONLY")
        check(requiredText(voyage, "providerOrigin") == "https://api.voyageai.com")
        check(requiredText(voyage, "providerEndpoint") == "POST /v1/contextualizedembeddings")
        check(requiredText(voyage, "embeddingInputStrategy") == "VOYAGE_CONTEXTUAL_DOCUMENT_BATCH_OVERLAP_0")
        check(voyage.path("transientAdjacentContextMaxPercent").intValue() == 0)
    }

    private fun validatePolicies(nodes: JsonNode) {
        check(nodes.isArray && nodes.size() == 3)
        val policies =
            nodes
                .values()
                .asSequence()
                .associateBy { requiredText(it, "policyId") }
        check(policies.keys.toList() == APPROVED_POLICY_IDS)
        check(policies.keys.none(APPROVED_PROFILE_IDS::contains))
        policies.values.forEach { policy ->
            check(policy.propertyNames().asSequence().toSet() == APPROVED_POLICY_FIELDS)
            val policyId = requiredText(policy, "policyId")
            check(policy.path("perRequestFallback").isBoolean)
            check(!policy.path("perRequestFallback").booleanValue()) {
                "$policyId must not fallback per request."
            }
            check(policy.path("providerOutageFallback").isBoolean)
            check(!policy.path("providerOutageFallback").booleanValue()) {
                "$policyId must not fallback on provider outage."
            }
            check(requiredText(policy, "queryProfileId") == requiredText(policy, "documentProfileId")) {
                "$policyId cannot mix query/document vector spaces."
            }
            check(requiredText(policy, "defaultProfileId") == requiredText(policy, "queryProfileId")) {
                "$policyId default profile drifted."
            }
            val outboundExpected = requiredText(policy, "defaultProfileId") == "voyage_context_4_1024_v1"
            check(
                policy.path("outboundProviderCalls").isBoolean &&
                    policy.path("outboundProviderCalls").booleanValue() == outboundExpected,
            )
            check(
                policy
                    .path("transition")
                    .propertyNames()
                    .asSequence()
                    .toSet() == APPROVED_TRANSITION_FIELDS,
            )
        }
        APPROVED_POLICY_TRANSITIONS.forEach { (policyId, expected) ->
            val transition = policies.getValue(policyId).path("transition")
            check(
                transition.path("allowed").isBoolean &&
                    transition.path("allowed").booleanValue() == expected.allowed,
            )
            check(
                transition.path("adminApprovalRequired").isBoolean &&
                    transition.path("adminApprovalRequired").booleanValue() ==
                    expected.adminApprovalRequired,
            )
            check(transition.path("targetProfileId").nullableText() == expected.targetProfileId)
            check(transition.path("trigger").nullableText() == expected.trigger)
        }
    }

    private fun textArray(node: JsonNode): List<String> {
        check(node.isArray && node.values().asSequence().all { it.isString && it.stringValue().isNotBlank() })
        return node
            .values()
            .asSequence()
            .map(JsonNode::stringValue)
            .toList()
    }

    private fun validateChunking(node: JsonNode) {
        check(node.propertyNames().asSequence().toSet() == APPROVED_CHUNKING_FIELDS)
        check(requiredText(node, "boundaryStrategy") == "MARKDOWN_HEADING_PARAGRAPH")
        check(node.path("minimumTargetTokens").intValue() == 400)
        check(node.path("maximumTargetTokens").intValue() == 600)
        check(node.path("overlapPercent").intValue() == 0)
        check(node.path("tableSplitAllowed").isBoolean && !node.path("tableSplitAllowed").booleanValue())
        check(requiredText(node, "oversizedTablePolicy") == "REJECT")
    }

    private fun validateSourceMetadata(node: JsonNode) {
        check(node.propertyNames().asSequence().toSet() == APPROVED_SOURCE_METADATA_FIELDS)
        check(node.path("maximumItems").isIntegralNumber && node.path("maximumItems").intValue() == 30)
        check(requiredText(node, "publicSourceType") == "PROJECT_SOURCE_CARD")
        listOf("queryParametersAllowed", "rawChunkIncluded", "rawUpstreamBodyIncluded").forEach { field ->
            check(node.path(field).isBoolean && !node.path(field).booleanValue())
        }
    }

    private fun requiredText(
        node: JsonNode,
        field: String,
    ): String {
        val value = node.path(field)
        check(value.isString && value.stringValue().isNotBlank()) {
            "S4 RAG catalog text field is invalid."
        }
        return value.stringValue()
    }

    companion object {
        private const val MAX_CATALOG_BYTES = 262_144L
        private const val CATALOG_RESOURCE = "contracts/s4-rag-contract.v1.json"
        private const val CATALOG_SHA256_MANIFEST_RESOURCE = "contracts/s4-rag-contract.v1.sha256.json"
        private const val FORBIDDEN_PROFILE_ID = "voyage_context_3_1024_v1"
        private val APPROVED_PROFILE_IDS = listOf("bge_m3_local_1024_v1", "voyage_context_4_1024_v1")
        private val APPROVED_POLICY_IDS =
            listOf("bge_only_v1", "voyage_only_v1", "bge_then_voyage_on_sla_v1")
        private val APPROVED_GENERATION_STATUSES =
            listOf(
                "REGISTERED",
                "PLANNED",
                "MATERIALIZING",
                "MATERIALIZED",
                "EVAL_PASSED",
                "ACTIVE",
                "FAILED_FINAL",
                "DISABLED",
            )
        private val APPROVED_TOPIC_ALLOWLIST =
            listOf(
                "API",
                "DATA",
                "FINANCIAL_ENGINEERING",
                "METHODOLOGY",
                "PRODUCT_RISK",
                "RISK",
            )
        private val APPROVED_ROOT_FIELDS =
            setOf(
                "answerModes",
                "askRequest",
                "canonicalChunking",
                "contractId",
                "dimension",
                "embeddingInputStrategies",
                "embeddingOperations",
                "forbiddenProfileIds",
                "generationStatuses",
                "policies",
                "policyIds",
                "profiles",
                "profileIds",
                "schemaVersion",
                "sourceMetadata",
                "topicAllowlist",
            )
        private val APPROVED_PROFILE_FIELDS =
            setOf(
                "artifactFormat",
                "canonicalChunkOverlapPercent",
                "dimension",
                "embeddingInputStrategy",
                "externalProvider",
                "freeTokenEligible",
                "model",
                "operationAllowlist",
                "profileId",
                "provider",
                "providerEndpoint",
                "providerOrigin",
                "transientAdjacentContextMaxPercent",
                "trustRemoteCode",
                "vectorSpace",
            )
        private val APPROVED_POLICY_FIELDS =
            setOf(
                "defaultProfileId",
                "documentProfileId",
                "outboundProviderCalls",
                "perRequestFallback",
                "policyId",
                "providerOutageFallback",
                "queryProfileId",
                "transition",
            )
        private val APPROVED_TRANSITION_FIELDS =
            setOf(
                "adminApprovalRequired",
                "allowed",
                "targetProfileId",
                "trigger",
            )
        private val APPROVED_POLICY_TRANSITIONS =
            mapOf(
                "bge_only_v1" to PolicyTransition(false, false, null, null),
                "voyage_only_v1" to PolicyTransition(false, false, null, null),
                "bge_then_voyage_on_sla_v1" to
                    PolicyTransition(
                        adminApprovalRequired = true,
                        allowed = true,
                        targetProfileId = "voyage_context_4_1024_v1",
                        trigger = "BGE_WARM_P95_SLA_FAILED_AND_VOYAGE_EVAL_PASSED",
                    ),
            )
        private val APPROVED_CHUNKING_FIELDS =
            setOf(
                "boundaryStrategy",
                "maximumTargetTokens",
                "minimumTargetTokens",
                "overlapPercent",
                "oversizedTablePolicy",
                "tableSplitAllowed",
            )
        private val APPROVED_SOURCE_METADATA_FIELDS =
            setOf(
                "maximumItems",
                "publicSourceType",
                "queryParametersAllowed",
                "rawChunkIncluded",
                "rawUpstreamBodyIncluded",
            )
        private val APPROVED_ASK_FIELDS =
            setOf(
                "forbiddenBodyFields",
                "idempotencyHeader",
                "idempotencyKeyPattern",
                "maximumQuestionUnicodeScalars",
                "maximumQuestionUtf8Bytes",
                "minimumQuestionUnicodeScalars",
                "normalization",
                "relatedSymbolsMaximumItems",
                "relatedSymbolsPattern",
                "route",
                "topicsMaximumItems",
            )
        private val REQUIRED_FORBIDDEN_ASK_FIELDS =
            setOf(
                "embeddingProfileId",
                "embeddingPolicyId",
                "profileId",
                "policyId",
                "topK",
                "sourceTier",
                "provider",
                "model",
            )

        private fun sha256(bytes: ByteArray): String =
            MessageDigest
                .getInstance("SHA-256")
                .digest(bytes)
                .joinToString("") { byte -> "%02x".format(java.util.Locale.ROOT, byte.toInt() and 0xff) }

        private fun resourceBytes(resource: String): ByteArray = ClassPathResource(resource).inputStream.use { it.readBytes() }

        internal fun fromBytes(
            catalogBytes: ByteArray,
            digestManifestBytes: ByteArray,
        ): RagContractCatalog = RagContractCatalog(catalogBytes, digestManifestBytes)
    }
}

private data class PolicyTransition(
    val adminApprovalRequired: Boolean,
    val allowed: Boolean,
    val targetProfileId: String?,
    val trigger: String?,
)

private fun JsonNode.nullableText(): String? =
    when {
        isNull -> null
        isString -> stringValue()
        else -> error("S4 RAG nullable text field is invalid.")
    }
