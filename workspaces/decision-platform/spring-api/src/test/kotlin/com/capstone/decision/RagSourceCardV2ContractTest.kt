package com.capstone.decision

import com.capstone.decision.infrastructure.rag.RagSourceCardV2Contract
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import org.springframework.core.io.ClassPathResource
import java.nio.file.Files
import java.nio.file.Path

class RagSourceCardV2ContractTest {
    private val repositoryRoot: Path =
        Path
            .of("../../..")
            .toAbsolutePath()
            .normalize()

    @Test
    fun `Spring validator accepts all v2 authority variants`() {
        listOf(
            "rag-source-card-v2.official-migration.valid.json",
            "rag-source-card-v2.naver-official.valid.json",
            "rag-source-card-v2.scholarly.valid.json",
        ).forEach { fileName ->
            val payload = Files.readAllBytes(repositoryRoot.resolve("contracts/examples/$fileName"))

            val card = RagSourceCardV2Contract.validate(payload)

            assertThat(card.schemaVersion).isEqualTo("2")
            assertThat(card.cardVariant)
                .isIn("OFFICIAL_UPSTREAM_CARD", "SCHOLARLY_PRIMARY_CARD")
        }
    }

    @Test
    fun `Spring validator rejects every generated v2 negative fixture`() {
        val invalidDirectory = repositoryRoot.resolve("contracts/examples/invalid")
        val invalidFiles =
            Files
                .list(invalidDirectory)
                .use { paths ->
                    paths
                        .filter {
                            it.fileName
                                .toString()
                                .matches(Regex("""rag-source-card-v2\..+\.invalid\.json"""))
                        }.sorted()
                        .toList()
                }
        assertThat(invalidFiles).hasSizeGreaterThanOrEqualTo(24)

        invalidFiles.forEach { path ->
            assertThatThrownBy { RagSourceCardV2Contract.validate(Files.readAllBytes(path)) }
                .isInstanceOf(IllegalArgumentException::class.java)
                .hasMessageContaining("source card v2")
        }
    }

    @Test
    fun `Spring consumes the exact canonical v2 schema bytes`() {
        val canonical =
            Files.readAllBytes(
                repositoryRoot.resolve("contracts/schemas/rag-source-card-v2.schema.json"),
            )
        val classpath =
            ClassPathResource("contracts/rag-source-card-v2.schema.json")
                .inputStream
                .use { it.readBytes() }

        assertThat(classpath).containsExactly(*canonical)
    }
}
