package com.capstone.decision.application.brokerage

import com.capstone.decision.domain.risk.OrderIntentSnapshot
import java.time.Instant

interface BrokerageIdempotencyIdentityPort {
    fun identity(
        actorUserId: String,
        rawKey: String,
        command: SubmitMockOrderCommand,
    ): BrokerageIdempotencyIdentity
}

data class BrokerageGatewaySubmitRequest(
    val requestId: String,
    val orderId: String,
    val ownerUserId: String,
    val accountId: String,
    val orderIntent: OrderIntentSnapshot,
)

data class BrokerageGatewaySubmitResult(
    val orderId: String,
    val providerOrderRefHash: String,
    val trId: String,
    val receivedAt: Instant,
)

data class BrokerageGatewayCancelRequest(
    val requestId: String,
    val orderId: String,
    val ownerUserId: String,
    val accountId: String,
)

data class BrokerageGatewayCancelResult(
    val orderId: String,
    val status: String,
    val receivedAt: Instant,
)

data class BrokerageGatewayBalanceRequest(
    val requestId: String,
    val ownerUserId: String,
    val accountId: String,
)

data class BrokerageGatewayBalanceResult(
    val accountId: String,
    val cashKrw: Long,
    val portfolioEquityKrw: Long,
    val marginRequirementKrw: Long,
    val positions: List<MockBalancePositionProjection>,
    val observedAt: Instant,
    val sourceVersion: String,
)

data class BrokerageGatewayBuyableRequest(
    val requestId: String,
    val ownerUserId: String,
    val accountId: String,
    val symbol: String,
    val estimatedPriceKrw: Long,
)

data class BrokerageGatewayBuyableResult(
    val accountId: String,
    val symbol: String,
    val estimatedPriceKrw: Long,
    val buyableQuantity: Long,
    val buyableAmountKrw: Long,
    val cashKrw: Long,
    val observedAt: Instant,
    val sourceVersion: String,
)

interface BrokerageGatewayPort {
    fun submitMockOrder(request: BrokerageGatewaySubmitRequest): BrokerageGatewaySubmitResult

    fun cancelMockOrder(request: BrokerageGatewayCancelRequest): BrokerageGatewayCancelResult

    fun getMockBalance(request: BrokerageGatewayBalanceRequest): BrokerageGatewayBalanceResult

    fun getMockBuyable(request: BrokerageGatewayBuyableRequest): BrokerageGatewayBuyableResult
}

/**
 * 연결 확인이 KIS 에서 실제로 읽은 모의계좌 잔고. 계좌번호·원문은 없다. `positionsComplete` 가 false 면
 * 보유 종목이 한 쪽을 넘어 이 값을 위험 잔고 관측으로 쓰지 않는다.
 */
data class MockConnectionProof(
    val cashKrw: Long,
    val portfolioEquityKrw: Long,
    val positions: List<MockConnectionPosition>,
    val positionsComplete: Boolean,
)

data class MockConnectionPosition(
    val symbol: String,
    val quantity: Long,
    val marketValueKrw: Long,
)

/**
 * 연결 확인이 사용자가 고칠 수 있는 이유로 실패했다. `reason` 은 고정 목록이다:
 * APP_KEY_REJECTED, ACCOUNT_REJECTED, RATE_LIMITED, KIS_UNAVAILABLE.
 */
class MockConnectionRejectedException(
    val reason: String,
    cause: Throwable? = null,
) : RuntimeException("KIS_MOCK connection check was rejected: $reason", cause)

/**
 * FULL 잔고 화면이 오래된 관측을 보이지 않도록, 소유자 자격증명에 묶인 계좌의 관측이 오래됐으면 KIS
 * 잔고조회 한 번으로 새로 남긴다. 개인 스택에는 구현이 없다(자격증명 행이 없다).
 */
interface OwnerBalanceRefreshPort {
    fun refreshIfStale(
        ownerUserId: String,
        accountId: String,
        requestId: String,
    )
}

/** A read-only provider proof for the exact current owner's stored KIS_MOCK account. */
interface MockCredentialConnectionPort {
    fun verify(
        requestId: String,
        ownerUserId: String,
        accountId: String,
    ): MockConnectionProof?
}

enum class MockCredentialCertificationStatus {
    PASS,
    FAILED,
    RECOVERY_REQUIRED,
}

data class MockCredentialCertificationProof(
    val accountId: String,
    val certificationId: String,
    val status: MockCredentialCertificationStatus,
    val receiptSha256: String,
    val sessionDate: String,
    val quoteCalls: Int,
    val brokerageCalls: Int,
    val tokenCalls: Int,
    val failureCode: String,
)

/** One server-fixed one-share KIS_MOCK test under the current user's encrypted credential. */
interface MockCredentialCertificationPort {
    fun certify(
        requestId: String,
        ownerUserId: String,
        accountId: String,
        certificationId: String,
        sessionDate: String,
        recovery: Boolean,
    ): MockCredentialCertificationProof
}

interface BrokerageOrderPersistencePort {
    fun findIdempotencyResult(
        actorUserId: String,
        scopeHash: String,
        ownerScopeHash: String,
        now: java.time.Instant,
    ): StoredBrokerageIdempotencyResult?

    fun persist(request: BrokerageOrderWriteRequest)

    fun recordProviderOutcome(request: BrokerageProviderOutcomeRequest): OrderDetailProjection

    fun findOrderableDecisionAccountId(
        actorUserId: String,
        decisionId: String,
    ): String?

    fun findOwnedProjection(
        actorUserId: String,
        orderId: String,
    ): OrderDetailProjection?

    fun cancelOwnedOrder(
        actor: BrokerageActor,
        orderId: String,
        cancelledAt: java.time.Instant,
    ): OrderDetailProjection

    fun findOwnedBalance(
        actorUserId: String,
        accountId: String,
    ): StoredMockBalance?
}
