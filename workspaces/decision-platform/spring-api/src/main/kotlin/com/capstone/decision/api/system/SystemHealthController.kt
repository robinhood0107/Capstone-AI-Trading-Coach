package com.capstone.decision.api.system

import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import java.time.OffsetDateTime
import java.time.ZoneId

// 왜: swagger smoke와 프론트 상태 배지가 인증된 공통 envelope를 검증할 최소 API가 필요하다.
@RestController
@RequestMapping("/api/v1/system")
class SystemHealthController {
    @GetMapping("/health")
    fun health(): SystemHealthResponse =
        // 왜: S0.3은 walking skeleton이므로 외부 서비스 연결 전 고정 UP 상태로 계약 형태만 잠근다.
        SystemHealthResponse(
            asOf = OffsetDateTime.now(ZoneId.of("Asia/Seoul")),
            pythonService = "UP",
            brokerage = "UP",
            killSwitchActive = false,
            dataFreshness =
                DataFreshnessResponse(
                    priceFresh = true,
                    signalFresh = true,
                    ragFresh = true,
                ),
            degradedFeatures = emptyList(),
        )
}

// 왜: API 명세 2.7의 상태 응답 형태를 코드 타입으로 먼저 고정한다.
data class SystemHealthResponse(
    val asOf: OffsetDateTime,
    val pythonService: String,
    val brokerage: String,
    val killSwitchActive: Boolean,
    val dataFreshness: DataFreshnessResponse,
    val degradedFeatures: List<String>,
)

data class DataFreshnessResponse(
    val priceFresh: Boolean,
    val signalFresh: Boolean,
    val ragFresh: Boolean,
)
