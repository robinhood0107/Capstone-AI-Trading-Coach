package com.capstone.decision.api.automation

import com.capstone.decision.api.common.ApiException
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test

class AutomationRequestParserV3Test {
    private val parser = AutomationRequestParser()

    @Test
    fun `v3 policy parser accepts exact balanced policy and unlimited holding`() {
        val balanced =
            parser.parsePutPolicyV3(
                """{"capitalLimitKrw":10000000,"stopLossBps":500,"takeProfitBps":1000,"maxHoldingSessions":60,"atrPeriod":22,"atrMultiplierMilli":3000,"modelSellEnabled":true,"expectedVersion":0}""",
            )
        assertThat(balanced.maxHoldingSessions).isEqualTo(60)
        assertThat(balanced.atrMultiplierMilli).isEqualTo(3_000)
        val unlimited =
            parser.parsePutPolicyV3(
                """{"capitalLimitKrw":10000000,"stopLossBps":800,"takeProfitBps":1500,"maxHoldingSessions":0,"atrPeriod":22,"atrMultiplierMilli":3500,"modelSellEnabled":false,"expectedVersion":1}""",
            )
        assertThat(unlimited.maxHoldingSessions).isZero()
        assertThat(unlimited.modelSellEnabled).isFalse()
    }

    @Test
    fun `v3 policy parser rejects multiplier unit range unknown and duplicate fields`() {
        val invalid =
            listOf(
                """{"capitalLimitKrw":10000000,"stopLossBps":500,"takeProfitBps":1000,"maxHoldingSessions":60,"atrPeriod":22,"atrMultiplierMilli":3050,"modelSellEnabled":true,"expectedVersion":0}""",
                """{"capitalLimitKrw":10000000,"stopLossBps":500,"takeProfitBps":1000,"maxHoldingSessions":1261,"atrPeriod":22,"atrMultiplierMilli":3000,"modelSellEnabled":true,"expectedVersion":0}""",
                """{"capitalLimitKrw":10000000,"stopLossBps":500,"takeProfitBps":1000,"maxHoldingSessions":60,"atrPeriod":22,"atrMultiplierMilli":3000,"modelSellEnabled":true,"expectedVersion":0,"extra":1}""",
                """{"capitalLimitKrw":10000000,"capitalLimitKrw":10000000,"stopLossBps":500,"takeProfitBps":1000,"maxHoldingSessions":60,"atrPeriod":22,"atrMultiplierMilli":3000,"modelSellEnabled":true,"expectedVersion":0}""",
            )
        invalid.forEach { body ->
            assertThatThrownBy { parser.parsePutPolicyV3(body) }.isInstanceOf(ApiException::class.java)
        }
    }
}
