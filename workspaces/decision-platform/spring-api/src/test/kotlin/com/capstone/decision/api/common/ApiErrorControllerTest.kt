package com.capstone.decision.api.common

import jakarta.servlet.RequestDispatcher
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.CsvSource
import org.springframework.mock.web.MockHttpServletRequest

class ApiErrorControllerTest {
    @ParameterizedTest
    @CsvSource(
        "400,VALIDATION_ERROR",
        "401,UNAUTHORIZED",
        "403,FORBIDDEN",
        "404,NOT_FOUND",
        "405,VALIDATION_ERROR",
        "415,VALIDATION_ERROR",
        "409,CONFLICT",
        "429,RATE_LIMITED",
        "500,INTERNAL_ERROR",
    )
    fun `container errors keep their status and a code that matches it`(
        status: Int,
        code: String,
    ) {
        val request =
            MockHttpServletRequest("GET", "/error").apply {
                setAttribute(RequestDispatcher.ERROR_STATUS_CODE, status)
                setAttribute(RequestDispatcher.ERROR_REQUEST_URI, "/api/v1/unknown")
            }
        val response = ApiErrorController().handleError(request)
        assertEquals(status, response.statusCode.value())
        assertEquals(code, response.body?.error?.code)
    }
}
