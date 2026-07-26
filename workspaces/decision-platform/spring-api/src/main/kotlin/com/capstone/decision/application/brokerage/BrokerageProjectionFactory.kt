package com.capstone.decision.application.brokerage

import com.capstone.decision.domain.risk.CanonicalJson
import org.springframework.stereotype.Component
import tools.jackson.databind.ObjectMapper
import java.time.Instant

@Component
class BrokerageProjectionFactory(
    private val objectMapper: ObjectMapper,
) {
    fun createSubmitted(
        orderId: String,
        accountId: String,
        submittedAt: Instant,
    ): MockOrderProjection =
        MockOrderProjection(
            orderId = orderId,
            accountId = accountId,
            brokerageMode = "KIS_MOCK",
            status = "SUBMITTED",
            submittedAt = submittedAt,
        )

    fun canonicalJson(projection: MockOrderProjection): String =
        CanonicalJson.encode(
            mapOf(
                "brokerageMode" to projection.brokerageMode,
                "accountId" to projection.accountId,
                "orderId" to projection.orderId,
                "status" to projection.status,
                "submittedAt" to projection.submittedAt,
            ),
        )

    fun fromCanonicalJson(payload: String): MockOrderProjection {
        val node = objectMapper.readTree(payload)
        return MockOrderProjection(
            orderId = node.path("orderId").stringValue(),
            accountId = node.path("accountId").stringValue(),
            brokerageMode = node.path("brokerageMode").stringValue(),
            status = node.path("status").stringValue(),
            submittedAt = Instant.parse(node.path("submittedAt").stringValue()),
        )
    }
}
