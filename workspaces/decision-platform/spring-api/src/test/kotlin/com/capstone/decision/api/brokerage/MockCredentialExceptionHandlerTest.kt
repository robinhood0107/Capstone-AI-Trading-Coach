package com.capstone.decision.api.brokerage

import com.capstone.decision.application.brokerage.BrokerageUnavailableException
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.springframework.dao.DataAccessException
import org.springframework.mock.web.MockHttpServletRequest
import org.springframework.web.bind.annotation.RestControllerAdvice
import java.sql.SQLException

class MockCredentialExceptionHandlerTest {
    private val handler = MockCredentialExceptionHandler()
    private val request = MockHttpServletRequest("PUT", "/api/v1/brokerage/mock/credential")

    private fun sql(
        message: String,
        state: String,
    ) = object : DataAccessException("sensitive-value", SQLException(message, state)) {}

    @Test
    fun `pending rotation is a sanitized conflict and other database errors are unavailable`() {
        val conflict = handler.database(sql("sensitive-value", "40001"), request)
        assertEquals(409, conflict.statusCode.value())
        assertEquals("CONFLICT", conflict.body?.error?.code)
        assertTrue(
            conflict.body
                ?.error
                ?.details
                .orEmpty()
                .isEmpty(),
        )
        assertFalse(conflict.body.toString().contains("sensitive-value"))

        val failed = handler.database(sql("sensitive-value", "08006"), request)
        assertEquals(503, failed.statusCode.value())
        assertEquals("BROKERAGE_UNAVAILABLE", failed.body?.error?.code)
        assertFalse(failed.body.toString().contains("sensitive-value"))
    }

    @Test
    fun `an armed owner learns why the key cannot change without the database text`() {
        // PostgreSQL 드라이버가 실제로 싣는 모양: 첫 줄에 "ERROR: " 접두사, 다음 줄에 호출 위치.
        val armed =
            handler.database(
                sql(
                    "ERROR: mock credential cannot change while armed\n  Where: PL/pgSQL function " +
                        "put_bound_mock_broker_credential_v2(text) line 20 at RAISE",
                    "40001",
                ),
                request,
            )
        assertEquals(409, armed.statusCode.value())
        assertEquals("CONFLICT", armed.body?.error?.code)
        assertEquals(
            "AUTOMATION_ARMED",
            armed.body
                ?.error
                ?.details
                ?.get("reason"),
        )
        assertFalse(armed.body.toString().contains("PL/pgSQL"))

        val pending = handler.database(sql("ERROR: mock credential has pending execution", "40001"), request)
        assertEquals(
            "PENDING_EXECUTION",
            pending.body
                ?.error
                ?.details
                ?.get("reason"),
        )
        val reconciling = handler.database(sql("ERROR: mock credential has pending reconciliation", "40001"), request)
        assertEquals(
            "PENDING_RECONCILIATION",
            reconciling.body
                ?.error
                ?.details
                ?.get("reason"),
        )
    }

    @Test
    fun `a closed provider boundary is brokerage unavailable, never an authentication failure`() {
        val response = handler.brokerage(request)
        assertEquals(503, response.statusCode.value())
        assertEquals("BROKERAGE_UNAVAILABLE", response.body?.error?.code)
        // 연결 확인·해제·인증 경로도 같은 handler 를 탄다. 빠지면 /error 로 넘어가 401 이 된다.
        val covered =
            MockCredentialExceptionHandler::class.java
                .getAnnotation(RestControllerAdvice::class.java)
                .assignableTypes
                .toSet()
        assertEquals(
            setOf(
                MockCredentialController::class,
                MockCredentialConnectionController::class,
                MockCredentialDisconnectController::class,
                MockCredentialCertificationController::class,
            ),
            covered,
        )
        // 예외 타입이 바뀌어도 handler 대상에서 빠지지 않게 고정한다.
        assertTrue(
            MockCredentialExceptionHandler::class.java.methods.any { method ->
                method
                    .getAnnotation(org.springframework.web.bind.annotation.ExceptionHandler::class.java)
                    ?.value
                    ?.contains(BrokerageUnavailableException::class) == true
            },
        )
    }
}
