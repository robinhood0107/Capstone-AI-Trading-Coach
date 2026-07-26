package com.capstone.decision.infrastructure.brokerage

import com.capstone.decision.application.brokerage.BrokerageIdempotencyIdentity
import com.capstone.decision.application.brokerage.BrokerageIdempotencyIdentityPort
import com.capstone.decision.application.brokerage.SubmitMockOrderCommand
import com.capstone.decision.domain.risk.CanonicalJson
import org.springframework.stereotype.Component
import java.nio.charset.StandardCharsets
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * 주문 raw idempotency key와 actor 식별자는 purpose-version HMAC으로만 ledger에 남긴다.
 */
@Component
class BrokerageIdempotencyHasher(
    private val properties: BrokerageProperties,
) : BrokerageIdempotencyIdentityPort {
    override fun identity(
        actorUserId: String,
        rawKey: String,
        command: SubmitMockOrderCommand,
    ): BrokerageIdempotencyIdentity =
        BrokerageIdempotencyIdentity(
            scopeHash =
                hmac(
                    listOf(
                        BrokerageProperties.PURPOSE_VERSION,
                        "scope",
                        actorUserId,
                        rawKey,
                    ),
                ),
            ownerScopeHash =
                hmac(
                    listOf(
                        BrokerageProperties.PURPOSE_VERSION,
                        "owner",
                        actorUserId,
                    ),
                ),
            requestHash = requestHash(command),
        )

    private fun requestHash(command: SubmitMockOrderCommand): String =
        CanonicalJson.sha256(
            CanonicalJson.encode(
                mapOf(
                    "decisionId" to command.decisionId,
                    "orderIntent" to
                        mapOf(
                            "estimatedAmount" to command.orderIntent.estimatedAmount.toString(),
                            "estimatedPrice" to command.orderIntent.estimatedPrice.toString(),
                            "orderType" to command.orderIntent.orderType,
                            "quantity" to command.orderIntent.quantity.toString(),
                            "side" to command.orderIntent.side,
                            "strategyId" to command.orderIntent.strategyId,
                            "symbol" to command.orderIntent.symbol,
                            "timeframe" to command.orderIntent.timeframe,
                        ),
                    "userAcknowledgement" to
                        mapOf(
                            "warningsAccepted" to command.userAcknowledgement.warningsAccepted,
                        ),
                ),
            ),
        )

    private fun hmac(parts: List<String>): String {
        val keyBytes = properties.idempotencyScopeHmacKey.toByteArray(StandardCharsets.UTF_8)
        val messageBytes = parts.joinToString(separator = "\u0000").toByteArray(StandardCharsets.UTF_8)
        return try {
            val mac = Mac.getInstance("HmacSHA256")
            mac.init(SecretKeySpec(keyBytes, "HmacSHA256"))
            mac.doFinal(messageBytes).joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
        } finally {
            keyBytes.fill(0)
            messageBytes.fill(0)
        }
    }
}
