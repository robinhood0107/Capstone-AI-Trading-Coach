package com.capstone.decision.api.system

import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import java.time.OffsetDateTime
import java.time.ZoneId

@RestController
@RequestMapping("/api/v1/system")
class SystemHealthController {
    @GetMapping("/health")
    fun health(): SystemHealthResponse =
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
