package com.capstone.decision.application.brokerage

import com.capstone.decision.domain.brokerage.FillExecutionType
import com.capstone.decision.domain.brokerage.OrderFillState
import java.time.Instant

enum class BrokerageFillMode(
    val routeSegment: String,
) {
    KIS_MOCK("mock"),
    INTERNAL_PAPER("paper"),
}

data class StoredFillObservation(
    val observationId: String,
    val execRefHash: String,
    val execType: FillExecutionType,
    val fillQuantity: Long,
    val fillPriceKrw: Long?,
    val cumulativeQuantity: Long,
    val leavesQuantity: Long,
    val averageFillPriceKrw: Long?,
    val observedAt: Instant,
)

data class StoredOrderFillState(
    val orderId: String,
    val brokerageMode: BrokerageFillMode,
    val orderState: OrderFillState,
    val reconciliationStatus: String,
    val observationCount: Long,
    val observedFillQuantity: Long,
    val recomputedAverageFillPriceKrw: Long?,
    val providerFinalAverageFillPriceKrw: Long?,
    val observations: List<StoredFillObservation>,
    val hasMore: Boolean,
)

data class ExpectedOrderFillState(
    val status: String,
    val filledQuantity: Long,
    val leavesQuantity: Long,
    val unfilledTerminatedQuantity: Long,
    val averageFillPriceKrw: Long?,
    val reconciliationStatus: String,
    val appliedEventCount: Int,
    val hasMore: Boolean,
)

data class OrderFillApplyRequest(
    val actor: BrokerageActor,
    val orderId: String,
    val reconciledAt: Instant,
    val expectedFinal: ExpectedOrderFillState,
)

data class ReconciliationProjection(
    val status: String,
    val checkedAt: Instant?,
)

data class OrderFillReconciliationProjection(
    val orderId: String,
    val brokerageMode: String,
    val status: String,
    val filledQuantity: Long,
    val leavesQuantity: Long,
    val unfilledTerminatedQuantity: Long,
    val averageFillPriceKrw: Long?,
    val reconciliation: ReconciliationProjection,
    val appliedEventCount: Int,
    val hasMore: Boolean,
)

data class OrderFillRecord(
    val orderId: String,
    val brokerageMode: String,
    val symbol: String,
    val side: String,
    val fillQuantity: Long,
    val fillPriceKrw: Long,
    val fillAmountKrw: Long,
    val filledAt: Instant,
    val execRefHash: String,
)

data class OrderFillCursorPosition(
    val filledAt: Instant,
    val orderId: String,
    val execRefHash: String,
)

data class OrderFillPageRequest(
    val actor: BrokerageActor,
    val brokerageMode: BrokerageFillMode,
    val accountId: String,
    val fromInclusive: Instant,
    val toExclusive: Instant,
    val cursor: OrderFillCursorPosition?,
)

data class OrderFillPageProjection(
    val items: List<OrderFillRecord>,
    val nextCursor: String?,
)

/**
 * 대사 port는 ADMIN 현재 권한을 DB에서 재검증하고 advisory lock 아래 저장 관측만 원자 적용한다.
 * capability token과 직접 테이블 권한은 infrastructure 경계 밖으로 노출하지 않는다.
 */
interface OrderFillPersistencePort {
    fun acquireReconciliationLock(
        actor: BrokerageActor,
        orderId: String,
    )

    fun readReconciliationState(
        actor: BrokerageActor,
        orderId: String,
    ): StoredOrderFillState

    fun applyStoredFills(request: OrderFillApplyRequest): OrderFillReconciliationProjection

    fun readOwnedFills(request: OrderFillPageRequest): List<OrderFillRecord>
}

/**
 * cursor는 owner·mode·opaque account·기간·마지막 정렬키를 HMAC으로 결속해 raw offset을 노출하지 않는다.
 */
interface OrderFillCursorPort {
    fun encode(
        request: OrderFillPageRequest,
        last: OrderFillRecord,
    ): String

    fun decode(
        cursor: String,
        actor: BrokerageActor,
        brokerageMode: BrokerageFillMode,
        accountId: String,
        fromInclusive: Instant,
        toExclusive: Instant,
    ): OrderFillCursorPosition
}

class InvalidOrderFillCursorException : RuntimeException("Order fill cursor is invalid.")

class OrderFillLogicDivergenceException : RuntimeException("Order fill transition logic diverged.")
