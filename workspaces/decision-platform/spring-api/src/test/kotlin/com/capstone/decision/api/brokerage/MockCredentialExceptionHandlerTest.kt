package com.capstone.decision.api.brokerage

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Test
import org.springframework.dao.DataAccessException
import org.springframework.mock.web.MockHttpServletRequest
import java.sql.SQLException

class MockCredentialExceptionHandlerTest {
    private val handler = MockCredentialExceptionHandler()

    @Test
    fun `pending rotation is a sanitized conflict and other database errors are unavailable`() {
        val request = MockHttpServletRequest("PUT", "/api/v1/brokerage/mock/credential")
        val pending = object : DataAccessException("sensitive-value", SQLException("sensitive-value", "40001")) {}
        val conflict = handler.database(pending, request)
        assertEquals(409, conflict.statusCode.value())
        assertEquals("CONFLICT", conflict.body?.error?.code)
        assertFalse(conflict.body.toString().contains("sensitive-value"))

        val unavailable = object : DataAccessException("sensitive-value", SQLException("sensitive-value", "08006")) {}
        val failed = handler.database(unavailable, request)
        assertEquals(503, failed.statusCode.value())
        assertEquals("BROKERAGE_UNAVAILABLE", failed.body?.error?.code)
        assertFalse(failed.body.toString().contains("sensitive-value"))
    }
}
