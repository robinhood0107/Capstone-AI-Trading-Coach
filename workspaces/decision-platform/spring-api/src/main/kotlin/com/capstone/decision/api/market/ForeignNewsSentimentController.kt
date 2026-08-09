package com.capstone.decision.api.market

import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.market.ForeignNewsSentiment
import com.capstone.decision.application.market.ForeignNewsSentimentService
import com.capstone.decision.application.market.ForeignNewsSentimentUnavailableException
import com.capstone.decision.application.market.ForeignNewsSentimentValidationException
import com.capstone.decision.application.security.AppPrincipal
import io.swagger.v3.oas.annotations.Hidden
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.HttpStatus
import org.springframework.http.MediaType
import org.springframework.http.ResponseEntity
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import org.springframework.web.bind.annotation.RestControllerAdvice

/** foreign-news v1 addendum은 root OpenAPI bytes와 Decision/Risk surface를 바꾸지 않는 hidden route다. */
@RestController
@Hidden
@RequestMapping("/api/v2/market-evidence", produces = [MediaType.APPLICATION_JSON_VALUE])
class ForeignNewsSentimentController(
    private val service: ForeignNewsSentimentService,
) {
    /** authenticated owner의 sanitized aggregate만 읽으며 query parameter로 provider/filter를 열지 않는다. */
    @GetMapping("/{symbol}/foreign-news-sentiment")
    fun read(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable symbol: String,
        request: HttpServletRequest,
    ): ForeignNewsSentiment {
        require(request.queryString.isNullOrBlank())
        return service.read(principal.userId, symbol)
    }
}

data class ForeignNewsSentimentErrorResponse(
    val code: String,
    val message: String,
    val requestId: String,
)

@RestControllerAdvice(assignableTypes = [ForeignNewsSentimentController::class])
class ForeignNewsSentimentExceptionHandler {
    @ExceptionHandler(ForeignNewsSentimentValidationException::class, IllegalArgumentException::class)
    fun handleValidation(request: HttpServletRequest): ResponseEntity<ForeignNewsSentimentErrorResponse> =
        error(
            request = request,
            status = HttpStatus.BAD_REQUEST,
            code = "FOREIGN_NEWS_VALIDATION_FAILED",
            message = "Foreign-news symbol or request shape is invalid.",
        )

    @ExceptionHandler(ForeignNewsSentimentUnavailableException::class, RuntimeException::class)
    fun handleUnavailable(request: HttpServletRequest): ResponseEntity<ForeignNewsSentimentErrorResponse> =
        error(
            request = request,
            status = HttpStatus.SERVICE_UNAVAILABLE,
            code = "FOREIGN_NEWS_UNAVAILABLE",
            message = "Foreign-news sanitized runtime is unavailable.",
        )

    private fun error(
        request: HttpServletRequest,
        status: HttpStatus,
        code: String,
        message: String,
    ): ResponseEntity<ForeignNewsSentimentErrorResponse> =
        ResponseEntity
            .status(status)
            .body(
                ForeignNewsSentimentErrorResponse(
                    code = code,
                    message = message,
                    requestId = RequestIds.currentOrCreate(request),
                ),
            )
}
