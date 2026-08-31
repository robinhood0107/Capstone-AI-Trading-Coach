package com.capstone.decision.api.automation

import com.capstone.decision.api.common.ApiException
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test

class AutomationRequestParserV2Test {
    private val parser = AutomationRequestParser()

    @Test
    fun `policy parser accepts exact bounded values`() {
        val command =
            parser.parsePutPolicyV2(
                """{"capitalLimitKrw":1000000,"stopLossBps":500,"takeProfitBps":1000,"expectedVersion":0}""",
            )

        assertThat(command.capitalLimitKrw).isEqualTo(1_000_000)
        assertThat(command.stopLossBps).isEqualTo(500)
        assertThat(command.takeProfitBps).isEqualTo(1_000)
        assertThat(command.expectedVersion).isZero()
    }

    @Test
    fun `policy parser rejects non unit capital inverted exits and unknown fields`() {
        assertThatThrownBy {
            parser.parsePutPolicyV2(
                """{"capitalLimitKrw":10001,"stopLossBps":500,"takeProfitBps":1000,"expectedVersion":1}""",
            )
        }.isInstanceOf(ApiException::class.java)
        assertThatThrownBy {
            parser.parsePutPolicyV2(
                """{"capitalLimitKrw":1000000,"stopLossBps":1000,"takeProfitBps":500,"expectedVersion":1}""",
            )
        }.isInstanceOf(ApiException::class.java)
        assertThatThrownBy {
            parser.parsePutPolicyV2(
                """{"capitalLimitKrw":1000000,"stopLossBps":500,"takeProfitBps":1000,"expectedVersion":1,"extra":true}""",
            )
        }.isInstanceOf(ApiException::class.java)
        assertThatThrownBy {
            parser.parsePutPolicyV2(
                """{"capitalLimitKrw":1000000,"stopLossBps":500,"takeProfitBps":1000,"expectedVersion":-1}""",
            )
        }.isInstanceOf(ApiException::class.java)
    }

    @Test
    fun `v2 arm parser accepts exact identities and rejects legacy client authority`() {
        val command =
            parser.parseArmV2(
                """{"accountId":"acct_0123456789abcdef0123456789abcdef","policyId":"auto_pol_0123456789abcdef0123456789abcdef","expectedPolicyVersion":2,"expectedControlVersion":3}""",
            )
        assertThat(command.expectedPolicyVersion).isEqualTo(2)
        assertThat(command.expectedControlVersion).isEqualTo(3)

        assertThatThrownBy {
            parser.parseArmV2(
                """{"accountId":"acct_0123456789abcdef0123456789abcdef","policyId":"auto_pol_0123456789abcdef0123456789abcdef","expectedPolicyVersion":2,"expectedControlVersion":3,"strategyId":"client-owned"}""",
            )
        }.isInstanceOf(ApiException::class.java)
    }
}
