package com.capstone.decision.infrastructure.market

import com.capstone.decision.application.market.ForeignNewsLaneState
import com.capstone.decision.application.market.ForeignNewsSentiment
import com.capstone.decision.application.market.ForeignNewsSentimentReadPort
import com.capstone.decision.application.market.ForeignNewsSentimentUnavailableException
import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityRolePolicy
import com.capstone.decision.infrastructure.security.ActorRlsScope
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import org.springframework.transaction.annotation.Transactional
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.time.Instant

@Repository
class JdbcForeignNewsSentimentRepository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val objectMapper: ObjectMapper,
    private val actorRlsScope: ActorRlsScope,
) : ForeignNewsSentimentReadPort {
    /**
     * RLS actor를 authenticated owner로 고정한 뒤 V49 SECURITY DEFINER reader만 호출한다.
     * DB payload에 raw/provider field가 하나라도 있으면 API로 내보내지 않고 unavailable로 fail-closed한다.
     */
    @Transactional
    override fun findLatest(
        ownerUserId: String,
        symbol: String,
    ): ForeignNewsSentiment? {
        val jdbc = jdbc()
        actorRlsScope.open(
            jdbc,
            ownerUserId,
            ActorCapabilityBinding.target(
                "READ_FOREIGN_NEWS",
                "SYMBOL",
                symbol,
                ActorCapabilityRolePolicy.OWNER,
            ),
        )
        val payload =
            jdbc
                .query(
                    """
                    SELECT payload_json::text
                    FROM read_owned_foreign_news_sentiment(:ownerUserId, :symbol)
                    """.trimIndent(),
                    mapOf("ownerUserId" to ownerUserId, "symbol" to symbol),
                ) { result, _ -> result.getString("payload_json") }
                .singleOrNull()
                ?: return null
        return parse(payload)
    }

    private fun parse(payload: String): ForeignNewsSentiment =
        try {
            val root = objectMapper.readTree(payload)
            exactFields(root, ROOT_FIELDS, "foreign-news root")
            val allowedUses = root.path("allowedUses")
            require(allowedUses.isArray && allowedUses.size() == 1 && allowedUses[0].stringValue() == "EXPLANATION_ONLY")
            require(root.path("articleMetadataStored").isBoolean && !root.path("articleMetadataStored").booleanValue())
            require(root.path("contractId").stringValue() == "foreign-news-sentiment-v1")
            require(root.path("decisionAuthority").stringValue() == "NONE")
            require(root.path("rawProviderDataStored").isBoolean && !root.path("rawProviderDataStored").booleanValue())
            require(root.path("riskDecisionHashIncluded").isBoolean && !root.path("riskDecisionHashIncluded").booleanValue())
            require(root.path("s5FeatureEligible").isBoolean && !root.path("s5FeatureEligible").booleanValue())
            require(root.path("schemaVersion").isIntegralNumber && root.path("schemaVersion").intValue() == 1)
            val symbol = root.requiredText("symbol")
            require(SYMBOL.matches(symbol))
            val lanesNode = root.path("lanes")
            require(lanesNode.isArray && lanesNode.size() == FOREIGN_NEWS_LANES.size)
            val lanes =
                lanesNode
                    .values()
                    .asSequence()
                    .mapIndexed { index, lane ->
                        exactFields(lane, LANE_FIELDS, "foreign-news lane")
                        val laneId = lane.requiredText("laneId")
                        val state = lane.requiredText("state")
                        require(laneId == FOREIGN_NEWS_LANES[index])
                        require(state in LANE_STATES)
                        ForeignNewsLaneState(laneId = laneId, state = state)
                    }.toList()
            val status = root.requiredText("status")
            require(status in setOf("AVAILABLE", "ABSTAIN"))
            require((status == "AVAILABLE") == lanes.any { it.state == "AVAILABLE" })
            ForeignNewsSentiment(
                allowedUses = listOf("EXPLANATION_ONLY"),
                articleMetadataStored = false,
                asOf = Instant.parse(root.requiredText("asOf")),
                contractId = "foreign-news-sentiment-v1",
                decisionAuthority = "NONE",
                lanes = lanes,
                rawProviderDataStored = false,
                riskDecisionHashIncluded = false,
                s5FeatureEligible = false,
                schemaVersion = 1,
                status = status,
                symbol = symbol,
            )
        } catch (error: Exception) {
            throw ForeignNewsSentimentUnavailableException()
        }

    private fun exactFields(
        node: JsonNode,
        expected: Set<String>,
        subject: String,
    ) {
        require(node.isObject && node.propertyNames().asSequence().toSet() == expected) {
            "$subject payload is invalid"
        }
    }

    private fun JsonNode.requiredText(field: String): String {
        val value = path(field).takeIf(JsonNode::isString)?.stringValue()
        return value ?: throw ForeignNewsSentimentUnavailableException()
    }

    private fun jdbc(): NamedParameterJdbcTemplate {
        val jdbc = jdbcProvider.getIfAvailable()
        return jdbc ?: throw ForeignNewsSentimentUnavailableException()
    }

    private companion object {
        val FOREIGN_NEWS_LANES =
            listOf(
                "FINNHUB_PERSONAL_LOCAL",
                "SEC_OFFICIAL",
                "FED_OFFICIAL",
                "GDELT_OFFLINE_REFERENCE",
            )
        val LANE_STATES = setOf("AVAILABLE", "ABSTAIN", "NOT_ACTIVATED")
        val SYMBOL = Regex("^[0-9A-Z._:-]{1,20}$")
        val LANE_FIELDS = setOf("laneId", "state")
        val ROOT_FIELDS =
            setOf(
                "allowedUses",
                "articleMetadataStored",
                "asOf",
                "contractId",
                "decisionAuthority",
                "lanes",
                "rawProviderDataStored",
                "riskDecisionHashIncluded",
                "s5FeatureEligible",
                "schemaVersion",
                "status",
                "symbol",
            )
    }
}
