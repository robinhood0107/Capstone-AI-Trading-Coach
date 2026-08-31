package com.capstone.decision.api.rag

import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.rag.RagHistoryNotFoundException
import com.capstone.decision.application.rag.RagV2Answer
import com.capstone.decision.application.rag.RagV2CorpusNotReadyException
import com.capstone.decision.application.rag.RagV2CorpusStatus
import com.capstone.decision.application.rag.RagV2DeleteTicket
import com.capstone.decision.application.rag.RagV2EffectiveConsent
import com.capstone.decision.application.rag.RagV2ExternalConsentRequiredException
import com.capstone.decision.application.rag.RagV2HistoryDetail
import com.capstone.decision.application.rag.RagV2HistoryPage
import com.capstone.decision.application.rag.RagV2ImportTicket
import com.capstone.decision.application.rag.RagV2RuntimeService
import com.capstone.decision.application.rag.RagV2VertexPreparation
import com.capstone.decision.application.rag.RagValidationException
import com.capstone.decision.application.security.AppPrincipal
import io.swagger.v3.oas.annotations.Hidden
import io.swagger.v3.oas.annotations.Operation
import jakarta.servlet.http.HttpServletRequest
import org.slf4j.LoggerFactory
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

/**
 * v2 RAG 표면 가운데 대시보드가 쓰는 일곱 operation만 public OpenAPI에 노출한다.
 * owner 문서 import/delete ticket과 Vertex 준비는 아직 배포 가능한 기능이 아니므로 계속 숨긴다.
 */
@RestController
@RequestMapping("/api/v2/rag", produces = [MediaType.APPLICATION_JSON_VALUE])
class RagV2Controller(
    private val parser: RagRequestParser,
    private val service: RagV2RuntimeService,
) {
    @Operation(operationId = "ragV2CorpusStatus")
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
    @Operation(operationId = "ragV2EffectiveConsent")
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
    @Operation(operationId = "ragV2RecordExternalConsent")
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
    @Hidden
    fun issueImportTicket(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ResponseEntity<RagV2ImportTicket> {
        parser.requireNoQuery(request)
        val embeddingProfileId = parser.parseV2ImportTicketRequest(body.orEmpty())
        return ResponseEntity
            .status(HttpStatus.CREATED)
            .body(service.issueImportTicket(principal.userId, embeddingProfileId))
    }

    /**
     * delete capability도 owner principal과 DB-issued five-minute ticket으로만 연결한다.
     * raw path, delete reason, admin credential 또는 caller-supplied owner ID는 HTTP surface에 없다.
     */
    @PostMapping("/delete-tickets", consumes = [MediaType.APPLICATION_JSON_VALUE])
    @Hidden
    fun issueDeleteTicket(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ResponseEntity<RagV2DeleteTicket> {
        parser.requireNoQuery(request)
        val documentId = parser.parseV2DeleteTicketRequest(body.orEmpty())
        return ResponseEntity.status(HttpStatus.CREATED).body(service.issueDeleteTicket(principal.userId, documentId))
    }

    /**
     * Vertex physical-call packet은 authenticated owner의 exact ask body와 request ID에서만 prepare한다.
     * response에는 raw question/evidence를 넣지 않고, caller는 same request ID와 opaque scope header로만 ask를
     * resume할 수 있다.
     */
    @PostMapping("/vertex-preparations", consumes = [MediaType.APPLICATION_JSON_VALUE])
    @Hidden
    fun prepareVertexGeneration(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ResponseEntity<RagV2VertexPreparation> {
        parser.requireNoQuery(request)
        val preparation =
            service.prepareVertexGeneration(
                ownerUserId = principal.userId,
                requestId = parser.requireV2VertexRequestId(RequestIds.currentOrCreate(request)),
                command = parser.parseAsk(body.orEmpty()),
            )
        return ResponseEntity.status(HttpStatus.CREATED).body(preparation)
    }

    @Operation(operationId = "ragV2Ask")
    @PostMapping("/ask", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun ask(
        @AuthenticationPrincipal principal: AppPrincipal,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): RagV2Answer {
        parser.requireNoQuery(request)
        val command = parser.parseAsk(body.orEmpty())
        val vertexScopeClaimId = parser.parseV2VertexScopeClaim(request)
        val requestId = RequestIds.currentOrCreate(request)
        return service.ask(
            ownerUserId = principal.userId,
            requestId =
                if (vertexScopeClaimId == null) {
                    requestId
                } else {
                    parser.requireV2VertexRequestId(requestId)
                },
            command = command,
            vertexScopeClaimId = vertexScopeClaimId,
        )
    }

    @Operation(operationId = "ragV2ListHistory")
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

    @Operation(operationId = "ragV2GetHistory")
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

    @Operation(operationId = "ragV2DeleteHistory")
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
    private val logger = LoggerFactory.getLogger(javaClass)

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
    fun handleUnavailable(
        exception: RuntimeException,
        request: HttpServletRequest,
    ): ResponseEntity<RagV2ErrorResponse> {
        // 질문 본문, provider 응답, owner 문서, 예외 메시지는 남기지 않는다. 다만 어느 구간이
        // 닫혔는지는 클래스 이름으로 남긴다. 이것이 없으면 모든 실패가 구분 없는 503 하나로
        // 보여 fail-closed의 원인을 밖에서 알 방법이 없다.
        logger.warn(
            "rag v2 failed closed: {} caused by {}",
            exception.javaClass.simpleName,
            generateSequence(exception.cause) { it.cause }.lastOrNull()?.javaClass?.simpleName ?: "-",
        )
        return error(
            request = request,
            status = HttpStatus.SERVICE_UNAVAILABLE,
            code = "RAG_UNAVAILABLE",
            message = "RAG v2 runtime is unavailable.",
        )
    }

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
