package com.capstone.decision.api.common

import jakarta.servlet.http.HttpServletRequest
import org.slf4j.MDC
import java.time.LocalDate
import java.time.ZoneId
import java.util.UUID

// 요청 추적 ID 생성/조회 규칙을 한 곳에 둬 header, MDC, envelope가 같은 값을 공유한다.
object RequestIds {
    const val HEADER = "X-Request-Id"
    const val MDC_KEY = "requestId"
    private val zoneId: ZoneId = ZoneId.of("Asia/Seoul")

    // filter 밖에서 만들어진 오류도 기존 requestId를 최대한 재사용해야 추적이 이어진다.
    fun currentOrCreate(request: HttpServletRequest): String =
        MDC.get(MDC_KEY)
            ?: request.getHeader(HEADER)?.takeIf { it.isNotBlank() }
            ?: generate()

    // 로컬 데모 로그에서 날짜를 보고 요청 시점을 빠르게 가늠할 수 있게 한다.
    fun generate(): String = "req_${LocalDate.now(zoneId).toString().replace("-", "")}_${UUID.randomUUID()}"
}
