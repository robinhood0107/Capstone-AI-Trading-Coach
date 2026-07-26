package com.capstone.decision.application.brokerage.paper

import com.capstone.decision.domain.brokerage.PaperFillDecision
import com.capstone.decision.domain.risk.CanonicalJson
import org.springframework.stereotype.Component
import tools.jackson.databind.ObjectMapper
import java.time.Instant

@Component
class PaperProjectionFactory(
    private val objectMapper: ObjectMapper,
) {
    fun create(
        orderId: String,
        accountId: String,
        submittedAt: Instant,
        decision: PaperFillDecision,
    ): PaperOrderProjection =
        PaperOrderProjection(
            orderId = orderId,
            accountId = accountId,
            brokerageMode = "INTERNAL_PAPER",
            status = if (decision is PaperFillDecision.Filled) "FILLED" else "ACCEPTED",
            submittedAt = submittedAt,
            fill =
                (decision as? PaperFillDecision.Filled)?.let { fill ->
                    PaperFillProjection(
                        quantity = fill.quantity,
                        priceKrw = fill.priceKrw,
                        amountKrw = fill.amountKrw,
                        priceBasis = fill.priceBasis.name,
                        slippageBps = fill.slippageBps,
                        feeModel = fill.feeModel.name,
                        observedAt = fill.observedAt,
                    )
                },
        )

    fun canonicalJson(projection: PaperOrderProjection): String =
        CanonicalJson.encode(
            mapOf(
                "accountId" to projection.accountId,
                "brokerageMode" to projection.brokerageMode,
                "fill" to
                    projection.fill?.let { fill ->
                        mapOf(
                            "amountKrw" to fill.amountKrw.toString(),
                            "feeModel" to fill.feeModel,
                            "observedAt" to fill.observedAt,
                            "priceBasis" to fill.priceBasis,
                            "priceKrw" to fill.priceKrw.toString(),
                            "quantity" to fill.quantity.toString(),
                            "slippageBps" to fill.slippageBps,
                        )
                    },
                "orderId" to projection.orderId,
                "status" to projection.status,
                "submittedAt" to projection.submittedAt,
            ),
        )

    fun fromCanonicalJson(payload: String): PaperOrderProjection {
        val node = objectMapper.readTree(payload)
        val fillNode = node.path("fill")
        return PaperOrderProjection(
            orderId = node.path("orderId").stringValue(),
            accountId = node.path("accountId").stringValue(),
            brokerageMode = node.path("brokerageMode").stringValue(),
            status = node.path("status").stringValue(),
            submittedAt = Instant.parse(node.path("submittedAt").stringValue()),
            fill =
                if (fillNode.isNull || fillNode.isMissingNode) {
                    null
                } else {
                    PaperFillProjection(
                        quantity = fillNode.path("quantity").stringValue().toLong(),
                        priceKrw = fillNode.path("priceKrw").stringValue().toLong(),
                        amountKrw = fillNode.path("amountKrw").stringValue().toLong(),
                        priceBasis = fillNode.path("priceBasis").stringValue(),
                        slippageBps = fillNode.path("slippageBps").intValue(),
                        feeModel = fillNode.path("feeModel").stringValue(),
                        observedAt = Instant.parse(fillNode.path("observedAt").stringValue()),
                    )
                },
        )
    }
}
