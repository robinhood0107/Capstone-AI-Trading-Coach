package com.capstone.decision.api.rag

import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.rag.RagHistoryNotFoundException
import com.capstone.decision.application.rag.RagV2Answer
import com.capstone.decision.application.rag.RagV2CorpusNotReadyException
import com.capstone.decision.application.rag.RagV2CorpusStatus
import com.capstone.decision.application.rag.RagV2EffectiveConsent
import com.capstone.decision.application.rag.RagV2ExternalConsentRequiredException
import com.capstone.decision.application.rag.RagV2HistoryDetail
import com.capstone.decision.application.rag.RagV2HistoryPage
import com.capstone.decision.application.rag.RagV2ImportTicket
import com.capstone.decision.application.rag.RagV2RuntimeService
import com.capstone.decision.application.rag.RagValidationException
import com.capstone.decision.application.security.AppPrincipal
import io.swagger.v3.oas.annotations.Hidden
import jakarta.servlet.http.HttpServletRequest
import org.springframework.dao.DataAccessException
import org.springframework.http.HttpStatus
import org.springframework.http.MediaType
import org.springframework.http.ResponseEntity
import org.springframework.security.core.annotation.AuthenticationPrincipal
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import org.springframework.web.bind.annotation.RestControllerAdvice

@RestController
@Hidden
@RequestMapping("/api/v2/rag", produces = [MediaType.APPLICATION_JSON_VALUE])
class RagV2Controller(
    private val parser: RagRequestParser,
    private val service: RagV2RuntimeService,
) {
    @GetMapping("/corpus-status")
    fun corpusStatus(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): RagV2CorpusStatus {
        parser.requireNoQuery(request)
        return service.corpusStatus(principal.userId)
    }

    /**
     * external processor consent의 effective 상태는 authenticated owner 자신의 immutable event만 해석한다.
     */
    @GetMapping("/consent")
    fun effectiveConsent(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): RagV2EffectiveConsent {
        parser.requireNoQuery(request)
        return service.effectiveConsent(principal.userId)
    }

    /**
     * consent event identity는 body가 아니라 서버가 만들며, raw provider 또는 owner document를 받지 않는다.
     */
    @PostMapping("/consents", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun recordExternalConsent(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ResponseEntity<Void> {
        parser.requireNoQuery(request)
        service.recordExternalConsent(principal.userId, parser.parseV2ExternalConsent(body.orEmpty()))
        return ResponseEntity.noContent().build()
    }

    /**
     * 단회 ticket은 owner-local parse capability로만 반환하며 DB에는 SHA-256 hash만 남긴다.
     */
    @PostMapping("/import-tickets", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun issueImportTicket(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ResponseEntity<RagV2ImportTicket> {
        parser.requireNoQuery(request)
        parser.parseV2ImportTicketRequest(body.orEmpty())
        return ResponseEntity.status(HttpStatus.CREATED).body(service.issueImportTicket(principal.userId))
    }

    @PostMapping("/ask", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun ask(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): RagV2Answer {
        parser.requireNoQuery(request)
        val command = parser.parseAsk(body.orEmpty())
        return service.ask(
            ownerUserId = principal.userId,
            requestId = RequestIds.currentOrCreate(request),
            command = command,
        )
    }

    @GetMapping("/history")
    fun listHistory(
        @AuthenticationPrincipal principal: AppPrincipal,
        request: HttpServletRequest,
    ): RagV2HistoryPage {
        val query = parser.parseHistoryQuery(request)
        return service.listHistory(
            ownerUserId = principal.userId,
            cursor = query.cursor,
            limit = query.limit,
        )
    }

    @GetMapping("/history/{answerId}")
    fun getHistory(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable answerId: String,
        request: HttpServletRequest,
    ): RagV2HistoryDetail {
        parser.requireNoQuery(request)
        return service.getHistory(
            ownerUserId = principal.userId,
            answerId = parser.parseV2AnswerId(answerId),
        )
    }

    @DeleteMapping("/history/{answerId}")
    fun deleteHistory(
        @AuthenticationPrincipal principal: AppPrincipal,
        @PathVariable answerId: String,
        request: HttpServletRequest,
    ): ResponseEntity<Void> {
        parser.requireNoQuery(request)
        service.deleteHistory(
            ownerUserId = principal.userId,
            answerId = parser.parseV2AnswerId(answerId),
        )
        return ResponseEntity.noContent().build()
    }
}

data class RagV2ErrorResponse(
    val code: String,
    val message: String,
    val requestId: String,
)

@RestControllerAdvice(assignableTypes = [RagV2Controller::class])
class RagV2ExceptionHandler {
    @ExceptionHandler(RagValidationException::class)
    fun handleValidation(
        exception: RagValidationException,
        request: HttpServletRequest,
    ): ResponseEntity<RagV2ErrorResponse> =
        error(
            request = request,
            status = HttpStatus.BAD_REQUEST,
            code = "RAG_VALIDATION_FAILED",
            message =
                exception.violations
                    .joinToString(prefix = "Invalid RAG v2 request: ", limit = 4) {
                        "${it.field}=${it.reason}"
                    }.take(300),
        )

    @ExceptionHandler(RagV2CorpusNotReadyException::class)
    fun handleCorpusNotReady(request: HttpServletRequest): ResponseEntity<RagV2ErrorResponse> =
        error(
            request = request,
            status = HttpStatus.CONFLICT,
            code = "CORPUS_NOT_READY",
            message = "RAG v2 full corpus bundle is not ready.",
        )

    @ExceptionHandler(RagV2ExternalConsentRequiredException::class)
    fun handleExternalConsentRequired(request: HttpServletRequest): ResponseEntity<RagV2ErrorResponse> =
        error(
            request = request,
            status = HttpStatus.CONFLICT,
            code = "EXTERNAL_AI_CONSENT_REQUIRED",
            message = "External AI RAG v2 consent is required.",
        )

    @ExceptionHandler(RagHistoryNotFoundException::class)
    fun handleHistoryNotFound(request: HttpServletRequest): ResponseEntity<RagV2ErrorResponse> =
        error(
            request = request,
            status = HttpStatus.NOT_FOUND,
            code = "RAG_HISTORY_NOT_FOUND",
            message = "RAG v2 history item was not found.",
        )

    @ExceptionHandler(DataAccessException::class, RuntimeException::class)
    fun handleUnavailable(request: HttpServletRequest): ResponseEntity<RagV2ErrorResponse> =
        error(
            request = request,
            status = HttpStatus.SERVICE_UNAVAILABLE,
            code = "RAG_UNAVAILABLE",
            message = "RAG v2 runtime is unavailable.",
        )

    private fun error(
        request: HttpServletRequest,
        status: HttpStatus,
        code: String,
        message: String,
    ): ResponseEntity<RagV2ErrorResponse> =
        ResponseEntity
            .status(status)
            .body(
                RagV2ErrorResponse(
                    code = code,
                    message = message,
                    requestId = RequestIds.currentOrCreate(request),
                ),
            )
}
