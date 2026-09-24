package com.capstone.decision.api.brokerage

import com.capstone.decision.api.common.ApiException
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test

class MockCredentialRequestParserTest {
    private val parser = MockCredentialRequestParser()
    private val key = "K" + "A".repeat(19)
    private val secret = "S" + "B".repeat(39)
    private val account = "5" + "0".repeat(9)

    @Test
    fun `accepts only the three mock credential values`() {
        val parsed = parser.parse("""{"appKey":"$key","appSecret":"$secret","accountNo":"$account"}""")
        assertEquals(key, parsed.appKey)
        assertEquals(secret, parsed.appSecret)
        assertEquals(account, parsed.accountNo)
    }

    @Test
    fun `rejects caller selected owner account mode and duplicate fields`() {
        val base = """{"appKey":"$key","appSecret":"$secret","accountNo":"$account"""
        for (extra in listOf(",\"ownerUserId\":\"usr_other\"", ",\"accountId\":\"acct_other\"", ",\"mode\":\"KIS_LIVE\"")) {
            assertThrows(ApiException::class.java) { parser.parse(base + extra + "}") }
        }
        assertThrows(ApiException::class.java) { parser.parse(base + ",\"appKey\":\"$key\"}") }
    }

    @Test
    fun `rejects non numeric account and control characters`() {
        assertThrows(ApiException::class.java) {
            parser.parse("""{"appKey":"$key","appSecret":"$secret","accountNo":"123456789x"}""")
        }
        assertThrows(ApiException::class.java) {
            parser.parse("{\"appKey\":\"$key\",\"appSecret\":\"bad\\nsecret\",\"accountNo\":\"$account\"}")
        }
    }
}
