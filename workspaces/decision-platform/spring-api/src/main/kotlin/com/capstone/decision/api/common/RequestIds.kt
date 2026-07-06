package com.capstone.decision.api.common

import jakarta.servlet.http.HttpServletRequest
import org.slf4j.MDC
import java.time.LocalDate
import java.time.ZoneId
import java.util.UUID

object RequestIds {
    const val HEADER = "X-Request-Id"
    const val MDC_KEY = "requestId"
    private val zoneId: ZoneId = ZoneId.of("Asia/Seoul")

    fun currentOrCreate(request: HttpServletRequest): String =
        MDC.get(MDC_KEY)
            ?: request.getHeader(HEADER)?.takeIf { it.isNotBlank() }
            ?: generate()

    fun generate(): String = "req_${LocalDate.now(zoneId).toString().replace("-", "")}_${UUID.randomUUID()}"
}
