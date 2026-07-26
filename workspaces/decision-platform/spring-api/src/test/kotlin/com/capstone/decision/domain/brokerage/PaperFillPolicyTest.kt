package com.capstone.decision.domain.brokerage

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import java.time.Instant

class PaperFillPolicyTest {
    private val policy = PaperFillPolicy(slippageBps = 5)
    private val observedAt = Instant.parse("2030-01-02T03:04:05Z")

    @Test
    fun `MARKET BUY는 최근가에 5bps를 올림하고 출처를 기록한다`() {
        val decision =
            policy.decide(
                request = request(side = "BUY", orderType = "MARKET"),
                quote = quote(lastPriceKrw = 10_001, previousCloseKrw = 9_900),
            )

        val fill = decision as PaperFillDecision.Filled
        assertEquals(10_007, fill.priceKrw)
        assertEquals(20_014, fill.amountKrw)
        assertEquals(PaperPriceBasis.LAST_QUOTE, fill.priceBasis)
        assertEquals(5, fill.slippageBps)
        assertEquals(PaperFeeModel.NONE_V1, fill.feeModel)
        assertEquals(observedAt, fill.observedAt)
    }

    @Test
    fun `MARKET SELL은 직전 종가에 5bps를 내림한다`() {
        val decision =
            policy.decide(
                request = request(side = "SELL", orderType = "MARKET"),
                quote = quote(lastPriceKrw = null, previousCloseKrw = 19_999),
            )

        val fill = decision as PaperFillDecision.Filled
        assertEquals(19_989, fill.priceKrw)
        assertEquals(PaperPriceBasis.PREVIOUS_CLOSE, fill.priceBasis)
        assertEquals(5, fill.slippageBps)
    }

    @Test
    fun `LIMIT은 base 경계에서 전량 체결하고 slippage를 적용하지 않는다`() {
        val buy =
            policy.decide(
                request = request(side = "BUY", orderType = "LIMIT", limitPriceKrw = 20_000),
                quote = quote(lastPriceKrw = 20_000),
            ) as PaperFillDecision.Filled
        val sell =
            policy.decide(
                request = request(side = "SELL", orderType = "LIMIT", limitPriceKrw = 20_000),
                quote = quote(lastPriceKrw = 20_000),
            ) as PaperFillDecision.Filled

        assertEquals(20_000, buy.priceKrw)
        assertEquals(20_000, sell.priceKrw)
        assertEquals(0, buy.slippageBps)
        assertEquals(0, sell.slippageBps)
    }

    @Test
    fun `LIMIT 조건 미달은 ACCEPTED이며 fill을 만들지 않는다`() {
        val buy =
            policy.decide(
                request = request(side = "BUY", orderType = "LIMIT", limitPriceKrw = 19_999),
                quote = quote(lastPriceKrw = 20_000),
            )
        val sell =
            policy.decide(
                request = request(side = "SELL", orderType = "LIMIT", limitPriceKrw = 20_001),
                quote = quote(lastPriceKrw = 20_000),
            )

        assertEquals(PaperFillDecision.Accepted(PaperAcceptedReason.LIMIT_NOT_FILLED), buy)
        assertEquals(PaperFillDecision.Accepted(PaperAcceptedReason.LIMIT_NOT_FILLED), sell)
    }

    @Test
    fun `최근가와 종가가 모두 없거나 source가 불완전하면 fail closed한다`() {
        val absent =
            assertThrows<PaperFillPolicyException> {
                policy.decide(request(), quote(lastPriceKrw = null, previousCloseKrw = null))
            }
        val partial =
            assertThrows<PaperFillPolicyException> {
                policy.decide(request(), quote(lastPriceKrw = 10_000, completeness = "PARTIAL"))
            }

        assertEquals(PaperFillFailure.PRICE_UNAVAILABLE, absent.failure)
        assertEquals(PaperFillFailure.PRICE_UNAVAILABLE, partial.failure)
    }

    @Test
    fun `금액 곱셈 overflow는 명시적 계약 오류로 수렴한다`() {
        val error =
            assertThrows<PaperFillPolicyException> {
                policy.decide(
                    request = request(quantity = Long.MAX_VALUE),
                    quote = quote(lastPriceKrw = Long.MAX_VALUE),
                )
            }

        assertEquals(PaperFillFailure.ARITHMETIC_OVERFLOW, error.failure)
    }

    @Test
    fun `SELL slippage가 0원 체결가를 만들면 가격 source 오류로 닫는다`() {
        val error =
            assertThrows<PaperFillPolicyException> {
                policy.decide(
                    request = request(side = "SELL"),
                    quote = quote(lastPriceKrw = 1),
                )
            }

        assertEquals(PaperFillFailure.PRICE_UNAVAILABLE, error.failure)
    }

    @Test
    fun `설정과 입력 범위를 닫는다`() {
        assertThrows<IllegalArgumentException> { PaperFillPolicy(slippageBps = 101) }
        assertThrows<IllegalArgumentException> { PaperFillPolicy(slippageBps = -1) }
        val invalid =
            assertThrows<PaperFillPolicyException> {
                policy.decide(request(quantity = 0), quote(lastPriceKrw = 10_000))
            }
        assertEquals(PaperFillFailure.INVALID_INPUT, invalid.failure)
    }

    @Test
    fun `LIMIT 요청에는 가격이 필요하고 ACCEPTED에는 fill 정보가 없다`() {
        val invalid =
            assertThrows<PaperFillPolicyException> {
                policy.decide(
                    request = request(orderType = "LIMIT", limitPriceKrw = null),
                    quote = quote(lastPriceKrw = 10_000),
                )
            }
        val accepted =
            policy.decide(
                request = request(orderType = "LIMIT", limitPriceKrw = 9_999),
                quote = quote(lastPriceKrw = 10_000),
            ) as PaperFillDecision.Accepted

        assertEquals(PaperFillFailure.INVALID_INPUT, invalid.failure)
        assertNull(accepted.fill)
    }

    @Test
    fun `side orderType source fillability 24조합은 bounded 결과로 수렴한다`() {
        val outcomes =
            buildList {
                listOf("BUY", "SELL").forEach { side ->
                    listOf("MARKET", "LIMIT").forEach { orderType ->
                        listOf("LAST", "PREVIOUS", "ABSENT").forEach { source ->
                            listOf(true, false).forEach { fillable ->
                                val limit =
                                    if (side == "BUY") {
                                        if (fillable) 10_000L else 9_999L
                                    } else {
                                        if (fillable) 10_000L else 10_001L
                                    }
                                val result =
                                    runCatching {
                                        policy.decide(
                                            request =
                                                request(
                                                    side = side,
                                                    orderType = orderType,
                                                    limitPriceKrw = limit.takeIf { orderType == "LIMIT" },
                                                ),
                                            quote =
                                                when (source) {
                                                    "LAST" -> quote(lastPriceKrw = 10_000)
                                                    "PREVIOUS" -> quote(lastPriceKrw = null, previousCloseKrw = 10_000)
                                                    else -> quote(lastPriceKrw = null, previousCloseKrw = null)
                                                },
                                        )
                                    }
                                add("$side:$orderType:$source:$fillable" to result)
                            }
                        }
                    }
                }
            }

        assertEquals(24, outcomes.size)
        outcomes.forEach { (case, result) ->
            when {
                ":ABSENT:" in case ->
                    assertEquals(
                        PaperFillFailure.PRICE_UNAVAILABLE,
                        (result.exceptionOrNull() as PaperFillPolicyException).failure,
                    )
                ":LIMIT:" in case && case.endsWith("false") ->
                    assertEquals(PaperFillDecision.Accepted(PaperAcceptedReason.LIMIT_NOT_FILLED), result.getOrThrow())
                else -> assertTrue(result.getOrThrow() is PaperFillDecision.Filled)
            }
        }
    }

    private fun request(
        side: String = "BUY",
        orderType: String = "MARKET",
        quantity: Long = 2,
        limitPriceKrw: Long? = null,
    ): PaperFillRequest =
        PaperFillRequest(
            side = side,
            orderType = orderType,
            quantity = quantity,
            limitPriceKrw = limitPriceKrw,
        )

    private fun quote(
        lastPriceKrw: Long?,
        previousCloseKrw: Long? = null,
        completeness: String = "COMPLETE",
    ): PaperPriceObservation =
        PaperPriceObservation(
            observationId = "quote-s32",
            lastPriceKrw = lastPriceKrw,
            previousCloseKrw = previousCloseKrw,
            completeness = completeness,
            observedAt = observedAt,
        )
}
