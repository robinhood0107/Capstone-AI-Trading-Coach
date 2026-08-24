package com.capstone.decision.infrastructure.brokerage

import com.capstone.decision.application.brokerage.BrokerageIdempotencyIdentity
import com.capstone.decision.application.brokerage.BrokerageIdempotencyIdentityPort
import com.capstone.decision.application.brokerage.SubmitMockOrderCommand
import com.capstone.decision.application.brokerage.paper.PaperIdempotencyIdentityPort
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
) : BrokerageIdempotencyIdentityPort,
    PaperIdempotencyIdentityPort {
    override fun identity(
        actorUserId: String,
        rawKey: String,
        command: SubmitMockOrderCommand,
    ): BrokerageIdempotencyIdentity =
        identityForPurpose(
            purpose = BrokerageProperties.PURPOSE_VERSION,
            actorUserId = actorUserId,
            rawKey = rawKey,
            command = command,
        )

    override fun paperIdentity(
        actorUserId: String,
        rawKey: String,
        command: SubmitMockOrderCommand,
    ): BrokerageIdempotencyIdentity =
        identityForPurpose(
            purpose = PAPER_ORDER_PURPOSE_VERSION,
            actorUserId = actorUserId,
            rawKey = rawKey,
            command = command,
        )

    /**
     * Redis replay key는 현재 role/security version까지 HMAC에 묶어 권한 변경 전 응답을 재사용하지 못하게 한다.
     * owner admission scope는 사용자·purpose 단위로 유지해 권한 변경이 key quota를 분할하지 않게 한다.
     */
    fun replayIdentity(
        actorUserId: String,
        actorRole: String,
        securityVersion: Long,
        rawKey: String,
        purpose: BrokerageWriteReplayPurpose,
    ): BrokerageReplayIdentity {
        require(actorRole.matches(Regex("^[A-Z_]{1,32}$")))
        require(securityVersion > 0)
        return BrokerageReplayIdentity(
            scopeHash =
                hmac(
                    listOf(
                        purpose.purposeVersion,
                        "scope",
                        actorUserId,
                        actorRole,
                        securityVersion.toString(),
                        rawKey,
                    ),
                ),
            ownerScopeHash =
                hmac(
                    listOf(
                        purpose.purposeVersion,
                        "owner",
                        actorUserId,
                    ),
                ),
        )
    }

    private fun identityForPurpose(
        purpose: String,
        actorUserId: String,
        rawKey: String,
        command: SubmitMockOrderCommand,
    ): BrokerageIdempotencyIdentity =
        BrokerageIdempotencyIdentity(
            scopeHash =
                hmac(
                    listOf(
                        purpose,
                        "scope",
                        actorUserId,
                        rawKey,
                    ),
                ),
            ownerScopeHash =
                hmac(
                    listOf(
                        purpose,
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
            mac.doFinal(messageBytes).joinToString("") { byte -> "%02x".format(java.util.Locale.ROOT, byte.toInt() and 0xff) }
        } finally {
            keyBytes.fill(0)
            messageBytes.fill(0)
        }
    }

    private companion object {
        const val PAPER_ORDER_PURPOSE_VERSION = "BROKERAGE_PAPER_ORDER_SUBMIT/v1"
    }
}

data class BrokerageReplayIdentity(
    val scopeHash: String,
    val ownerScopeHash: String,
)

enum class BrokerageWriteReplayPurpose(
    val purposeVersion: String,
) {
    ORDER_CANCEL("BROKERAGE_ORDER_CANCEL/v1"),
    FILL_APPLY("BROKERAGE_FILL_APPLY/v1"),
    GENERIC_FINANCE_WRITE("FINANCE_WRITE_REPLAY/v1"),
}
