package com.capstone.decision.api.rag

import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.rag.WorldNewsPage
import com.capstone.decision.application.rag.WorldNewsService
import com.capstone.decision.application.rag.WorldNewsUnavailableException
import com.capstone.decision.application.rag.WorldNewsValidationException
import com.capstone.decision.application.security.AppPrincipal
import io.swagger.v3.oas.annotations.Operation
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.HttpStatus
import org.springframework.http.MediaType
import org.springframework.http.ResponseEntity
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController
import org.springframework.web.bind.annotation.RestControllerAdvice

@RestController
@RequestMapping("/api/v2/rag", produces = [MediaType.APPLICATION_JSON_VALUE])
class WorldNewsController(
    private val service: WorldNewsService,
) {
    @Operation(
        operationId = "ragV2WorldNews",
        summary = "세계 뉴스 조회",
        description = "발행일이 없어도 최초 관측 시각으로 조회한다. Decision, Signal, Risk, Order 권한은 없다.",
    )
    @GetMapping("/world-news")
    fun lookup(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestParam(required = false, defaultValue = "") q: String,
        @RequestParam(required = false, defaultValue = "20") limit: Int,
        request: HttpServletRequest,
    ): WorldNewsPage {
        require(principal.userId.isNotBlank())
        val allowed = setOf("q", "limit")
        if (request.parameterMap.keys.any { it !in allowed } || request.parameterMap.values.any { it.size != 1 }) {
            throw WorldNewsValidationException()
        }
        return service.lookup(q, limit)
    }
}

data class WorldNewsErrorResponse(
    val code: String,
    val message: String,
    val requestId: String,
)

@RestControllerAdvice(assignableTypes = [WorldNewsController::class])
class WorldNewsExceptionHandler {
    @ExceptionHandler(WorldNewsValidationException::class, IllegalArgumentException::class)
    fun validation(request: HttpServletRequest): ResponseEntity<WorldNewsErrorResponse> =
        error(request, HttpStatus.BAD_REQUEST, "WORLD_NEWS_VALIDATION_FAILED", "World-news query is invalid.")

    @ExceptionHandler(WorldNewsUnavailableException::class)
    fun unavailable(request: HttpServletRequest): ResponseEntity<WorldNewsErrorResponse> =
        error(request, HttpStatus.SERVICE_UNAVAILABLE, "WORLD_NEWS_UNAVAILABLE", "World-news lookup is unavailable.")

    private fun error(
        request: HttpServletRequest,
        status: HttpStatus,
        code: String,
        message: String,
    ): ResponseEntity<WorldNewsErrorResponse> =
        ResponseEntity.status(status).body(WorldNewsErrorResponse(code, message, RequestIds.currentOrCreate(request)))
}
