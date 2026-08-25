package com.capstone.decision

import com.capstone.decision.infrastructure.rag.RagContractCatalog
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import org.springframework.core.io.ClassPathResource
import tools.jackson.databind.json.JsonMapper
import tools.jackson.databind.node.ObjectNode
import java.security.MessageDigest

class RagContractCatalogTest {
    private val catalog = RagContractCatalog()
    private val objectMapper = JsonMapper.builder().build()

    @Test
    fun `canonical RAG catalog fixes two profiles and three policies`() {
        assertThat(catalog.profileIds)
            .containsExactly("bge_m3_local_1024_v1", "voyage_context_4_1024_v1")
        assertThat(catalog.policyIds)
            .containsExactly("bge_only_v1", "voyage_only_v1", "bge_then_voyage_on_sla_v1")
        assertThat(catalog.generationStatuses)
            .containsExactly(
                "REGISTERED",
                "PLANNED",
                "MATERIALIZING",
                "MATERIALIZED",
                "EVAL_PASSED",
                "ACTIVE",
                "FAILED_FINAL",
                "DISABLED",
            )
        assertThat(catalog.topicAllowlist)
            .containsExactly(
                "API",
                "DATA",
                "FINANCIAL_ENGINEERING",
                "METHODOLOGY",
                "PRODUCT_RISK",
                "RISK",
            )
        assertThat(catalog.dimension).isEqualTo(1024)
        assertThat(catalog.catalogSha256)
            .isEqualTo("9b9881f9b25b6486f20999f27c0dd7043048fc26491e33cf2af892817dabbe0a")
        assertThat(catalog.profileIds).doesNotContain("voyage_context_3_1024_v1")
    }

    @Test
    fun `public ask catalog keeps profile policy provider and topK server owned`() {
        assertThat(catalog.askForbiddenBodyFields)
            .contains(
                "embeddingProfileId",
                "embeddingPolicyId",
                "profileId",
                "policyId",
                "provider",
                "model",
                "topK",
                "sourceTier",
            )
    }

    @Test
    fun `catalog rejects duplicate keys unsafe provider artifacts and transition drift`() {
        val original = resourceBytes("contracts/s4-rag-contract.v1.json")

        val duplicate =
            original
                .toString(Charsets.UTF_8)
                .replace(
                    "\"schemaVersion\": 1,",
                    "\"schemaVersion\": 1,\n  \"schemaVersion\": 1,",
                ).toByteArray()
        assertThatThrownBy { RagContractCatalog.fromBytes(duplicate, manifestFor(duplicate)) }
            .isInstanceOf(RuntimeException::class.java)

        val unsafeArtifact = objectMapper.readTree(original).deepCopy() as ObjectNode
        (unsafeArtifact.at("/profiles/1") as ObjectNode).put("artifactFormat", "PICKLE")
        val unsafeArtifactBytes = objectMapper.writeValueAsBytes(unsafeArtifact)
        assertThatThrownBy {
            RagContractCatalog.fromBytes(
                unsafeArtifactBytes,
                manifestFor(unsafeArtifactBytes),
            )
        }.isInstanceOf(IllegalStateException::class.java)

        val transitionDrift = objectMapper.readTree(original).deepCopy() as ObjectNode
        (transitionDrift.at("/policies/0/transition") as ObjectNode).put("allowed", true)
        val transitionDriftBytes = objectMapper.writeValueAsBytes(transitionDrift)
        assertThatThrownBy {
            RagContractCatalog.fromBytes(
                transitionDriftBytes,
                manifestFor(transitionDriftBytes),
            )
        }.isInstanceOf(IllegalStateException::class.java)
    }

    @Test
    fun `catalog rejects a digest manifest that does not attest the loaded bytes`() {
        val original = resourceBytes("contracts/s4-rag-contract.v1.json")
        val manifest =
            objectMapper
                .readTree(resourceBytes("contracts/s4-rag-contract.v1.sha256.json"))
                .deepCopy() as ObjectNode
        manifest.put("sha256", "0".repeat(64))

        assertThatThrownBy {
            RagContractCatalog.fromBytes(
                original,
                objectMapper.writeValueAsBytes(manifest),
            )
        }.isInstanceOf(IllegalStateException::class.java)
            .hasMessageContaining("digest")
    }

    private fun manifestFor(catalogBytes: ByteArray): ByteArray {
        val manifest =
            objectMapper
                .readTree(resourceBytes("contracts/s4-rag-contract.v1.sha256.json"))
                .deepCopy() as ObjectNode
        manifest.put("sha256", sha256(catalogBytes))
        return objectMapper.writeValueAsBytes(manifest)
    }

    private fun resourceBytes(path: String): ByteArray = ClassPathResource(path).inputStream.use { it.readBytes() }

    private fun sha256(bytes: ByteArray): String =
        MessageDigest
            .getInstance("SHA-256")
            .digest(bytes)
            .joinToString("") { byte -> "%02x".format(java.util.Locale.ROOT, byte.toInt() and 0xff) }
}
