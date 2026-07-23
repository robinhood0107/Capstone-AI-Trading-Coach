package com.capstone.decision

import com.capstone.decision.domain.principle.EvidenceRequirement
import com.capstone.decision.infrastructure.principle.PrincipleCatalog
import com.capstone.decision.infrastructure.principle.PrincipleRuleJsonCodec
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import tools.jackson.databind.json.JsonMapper

class PrincipleRuleJsonCodecTest {
    private val objectMapper = JsonMapper.builder().build()
    private val codec = PrincipleRuleJsonCodec(objectMapper, PrincipleCatalog(objectMapper))

    @Test
    fun `legacy enabled evidence is required while disabled optional evidence stays optional`() {
        val decoded =
            codec.decode(
                """
                [
                  {"ruleId":"max_position_per_asset","ruleType":"POSITION_LIMIT","metric":"asset_weight",
                   "operator":"<=","threshold":0.2,"severity":"BLOCK","enabled":true},
                  {"ruleId":"negative_news_guard","ruleType":"NEWS_GUARD","metric":"negative_news_score",
                   "operator":"<=","threshold":0.7,"severity":"WARN","enabled":true},
                  {"ruleId":"disclosure_risk_guard","ruleType":"DISCLOSURE_GUARD","metric":"disclosure_risk_score",
                   "operator":"<=","threshold":0.7,"severity":"ALLOW","enabled":false}
                ]
                """.trimIndent(),
            )

        assertEquals(
            listOf(EvidenceRequirement.REQUIRED, EvidenceRequirement.REQUIRED, EvidenceRequirement.OPTIONAL),
            decoded.map { it.evidenceRequirement },
        )
    }

    @Test
    fun `new encoding makes inferred defaults explicit without changing semantic rules`() {
        val legacy =
            """
            [
              {"ruleId":"negative_news_guard","ruleType":"NEWS_GUARD","metric":"negative_news_score",
               "operator":"<=","threshold":0.70,"severity":"ALLOW","enabled":false}
            ]
            """.trimIndent()
        val normalized = codec.decode(legacy)
        val encoded = codec.encode(normalized)

        assertTrue(objectMapper.readTree(encoded).path(0).has("evidenceRequirement"))
        assertEquals(normalized, codec.decode(encoded))
    }

    @Test
    fun `storage decoding rejects fields outside legacy or current immutable shapes`() {
        val raw =
            """
            [
              {"ruleId":"negative_news_guard","ruleType":"NEWS_GUARD","metric":"negative_news_score",
               "operator":"<=","threshold":0.70,"severity":"ALLOW","enabled":false,
               "unexpected":"must-not-be-ignored"}
            ]
            """.trimIndent()

        assertThrows<IllegalStateException> { codec.decode(raw) }
    }
}
