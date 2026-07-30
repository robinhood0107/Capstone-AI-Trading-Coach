package com.capstone.decision.infrastructure.rag

import org.springframework.core.io.ClassPathResource
import org.springframework.stereotype.Component
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.security.MessageDigest

// RAG profile/policy는 DB나 요청 문자열이 아니라 contracts catalog의 exact bytes에서만 읽는다.
@Component
class RagContractCatalog(
    objectMapper: ObjectMapper,
) {
    final val profileIds: List<String>
    final val policyIds: List<String>
    final val dimension: Int
    final val askForbiddenBodyFields: Set<String>

    init {
        val catalogBytes = ClassPathResource(CATALOG_RESOURCE).inputStream.use { it.readBytes() }
        check(sha256(catalogBytes) == CATALOG_SHA256) {
            "S4 RAG canonical catalog digest mismatch."
        }
        val root = objectMapper.readTree(catalogBytes)
        check(requiredText(root, "contractId") == "s4-rag-contract/v1")
        check(root.path("schemaVersion").intValue() == 1)
        dimension = root.path("dimension").intValue()
        check(dimension == 1024)

        profileIds = textArray(root.path("profileIds"))
        policyIds = textArray(root.path("policyIds"))
        check(profileIds == APPROVED_PROFILE_IDS)
        check(policyIds == APPROVED_POLICY_IDS)
        check(textArray(root.path("forbiddenProfileIds")).toSet() == setOf(FORBIDDEN_PROFILE_ID))
        check(FORBIDDEN_PROFILE_ID !in profileIds)

        validateProfiles(root.path("profiles"))
        validatePolicies(root.path("policies"))
        askForbiddenBodyFields = textArray(root.path("askRequest").path("forbiddenBodyFields")).toSet()
        check(REQUIRED_FORBIDDEN_ASK_FIELDS.all(askForbiddenBodyFields::contains)) {
            "S4 RAG public ask body must not expose profile, policy, provider, or topK controls."
        }
    }

    private fun validateProfiles(nodes: JsonNode) {
        val profiles =
            nodes
                .values()
                .asSequence()
                .associateBy { requiredText(it, "profileId") }
        check(profiles.keys.toList() == APPROVED_PROFILE_IDS)
        profiles.values.forEach { profile ->
            check(profile.path("dimension").intValue() == 1024)
            check(requiredText(profile, "vectorSpace") == requiredText(profile, "profileId"))
            check(!profile.path("trustRemoteCode").booleanValue())
        }
        val bge = profiles.getValue("bge_m3_local_1024_v1")
        check(requiredText(bge, "provider") == "LOCAL")
        check(!bge.path("externalProvider").booleanValue())
        check(requiredText(bge, "embeddingInputStrategy") == "BGE_TRANSIENT_ADJACENT_CONTEXT_MAX_15")

        val voyage = profiles.getValue("voyage_context_4_1024_v1")
        check(requiredText(voyage, "provider") == "VOYAGE")
        check(requiredText(voyage, "model") == "voyage-context-4")
        check(voyage.path("externalProvider").booleanValue())
        check(requiredText(voyage, "providerOrigin") == "https://api.voyageai.com")
        check(requiredText(voyage, "providerEndpoint") == "POST /v1/contextualizedembeddings")
        check(requiredText(voyage, "embeddingInputStrategy") == "VOYAGE_CONTEXTUAL_DOCUMENT_BATCH_OVERLAP_0")
    }

    private fun validatePolicies(nodes: JsonNode) {
        val policies =
            nodes
                .values()
                .asSequence()
                .associateBy { requiredText(it, "policyId") }
        check(policies.keys.toList() == APPROVED_POLICY_IDS)
        check(policies.keys.none(APPROVED_PROFILE_IDS::contains))
        policies.values.forEach { policy ->
            val policyId = requiredText(policy, "policyId")
            check(!policy.path("perRequestFallback").booleanValue()) {
                "$policyId must not fallback per request."
            }
            check(!policy.path("providerOutageFallback").booleanValue()) {
                "$policyId must not fallback on provider outage."
            }
            check(requiredText(policy, "queryProfileId") == requiredText(policy, "documentProfileId")) {
                "$policyId cannot mix query/document vector spaces."
            }
            check(requiredText(policy, "defaultProfileId") == requiredText(policy, "queryProfileId")) {
                "$policyId default profile drifted."
            }
        }
        val transition = policies.getValue("bge_then_voyage_on_sla_v1").path("transition")
        check(transition.path("allowed").booleanValue())
        check(transition.path("adminApprovalRequired").booleanValue())
        check(requiredText(transition, "targetProfileId") == "voyage_context_4_1024_v1")
        check(requiredText(transition, "trigger") == "BGE_WARM_P95_SLA_FAILED_AND_VOYAGE_EVAL_PASSED")
    }

    private fun textArray(node: JsonNode): List<String> =
        node
            .values()
            .asSequence()
            .map(JsonNode::stringValue)
            .toList()

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
        private const val CATALOG_RESOURCE = "contracts/s4-rag-contract.v1.json"
        private const val CATALOG_SHA256 = "9b9881f9b25b6486f20999f27c0dd7043048fc26491e33cf2af892817dabbe0a"
        private const val FORBIDDEN_PROFILE_ID = "voyage_context_3_1024_v1"
        private val APPROVED_PROFILE_IDS = listOf("bge_m3_local_1024_v1", "voyage_context_4_1024_v1")
        private val APPROVED_POLICY_IDS =
            listOf("bge_only_v1", "voyage_only_v1", "bge_then_voyage_on_sla_v1")
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
                .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
    }
}
