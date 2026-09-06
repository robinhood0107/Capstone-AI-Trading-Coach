package com.capstone.decision.api.risk

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.risk.OwnerKillSwitchAccessPort
import com.capstone.decision.application.risk.OwnerStopSnapshot
import com.capstone.decision.application.security.AppPrincipal
import jakarta.servlet.http.HttpServletRequest
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestHeader
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import java.time.Instant

/** 사용자 ID와 전역 scope는 body로 받지 않는다. 서버 인증과 DB capability가 대상을 결정한다. */
@RestController
@RequestMapping("/api/v2/risk/kill-switch")
class OwnerKillSwitchController(
    private val repository: OwnerKillSwitchAccessPort,
) {
    private val parser = RiskRequestParser()

    @GetMapping
    fun read(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): ApiResponse<OwnerKillSwitchDto> {
        parser.requireNoQuery(request)
        val requestId = RequestIds.currentOrCreate(request)
        return ApiResponseFactory.success(requestId, repository.access(principal, null, null, requestId).dto())
    }

    @PostMapping(consumes = ["application/json"])
    fun change(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestHeader("X-Idempotency-Key", required = false) key: String?,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ApiResponse<OwnerKillSwitchDto> {
        parser.requireNoQuery(request)
        parser.requireIdempotencyKey(key)
        val parsed = parser.parseKillSwitchChange(body.orEmpty())
        val requestId = RequestIds.currentOrCreate(request)
        return ApiResponseFactory.success(requestId, repository.access(principal, parsed.active, key, requestId).dto())
    }
}

data class OwnerKillSwitchDto(
    val active: Boolean,
    val globalActive: Boolean,
    val effectiveActive: Boolean,
    val reasonClass: String,
    val changedAt: Instant,
)

private fun OwnerStopSnapshot.dto() = OwnerKillSwitchDto(active, globalActive, effectiveActive, reasonClass, changedAt)
