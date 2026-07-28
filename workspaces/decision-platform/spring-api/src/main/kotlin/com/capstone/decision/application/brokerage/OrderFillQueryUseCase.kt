package com.capstone.decision.application.brokerage

import org.springframework.stereotype.Service
import java.time.Instant

/**
 * owner-scoped fill history를 고정 정렬로 최대 50건만 공개하고 51번째 행은 next cursor 판정에만 쓴다.
 */
@Service
class OrderFillQueryUseCase(
    private val persistencePort: OrderFillPersistencePort,
    private val cursorPort: OrderFillCursorPort,
) {
    fun query(
        actor: BrokerageActor,
        brokerageMode: BrokerageFillMode,
        accountId: String,
        fromInclusive: Instant,
        toExclusive: Instant,
        cursor: String?,
    ): OrderFillPageProjection {
        val position =
            cursor?.let {
                cursorPort.decode(
                    cursor = it,
                    actor = actor,
                    brokerageMode = brokerageMode,
                    accountId = accountId,
                    fromInclusive = fromInclusive,
                    toExclusive = toExclusive,
                )
            }
        val request =
            OrderFillPageRequest(
                actor = actor,
                brokerageMode = brokerageMode,
                accountId = accountId,
                fromInclusive = fromInclusive,
                toExclusive = toExclusive,
                cursor = position,
            )
        val rows = persistencePort.readOwnedFills(request)
        if (rows.size > PAGE_SIZE + 1) {
            throw BrokerageUnavailableException("Order fill page exceeded its database bound.")
        }
        val items = rows.take(PAGE_SIZE)
        val nextCursor =
            if (rows.size > PAGE_SIZE) {
                cursorPort.encode(request, items.last())
            } else {
                null
            }
        return OrderFillPageProjection(items = items, nextCursor = nextCursor)
    }

    private companion object {
        const val PAGE_SIZE = 50
    }
}
