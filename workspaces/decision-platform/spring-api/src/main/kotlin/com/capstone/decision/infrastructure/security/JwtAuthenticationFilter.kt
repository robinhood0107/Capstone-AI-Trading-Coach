package com.capstone.decision.infrastructure.security

import com.capstone.decision.api.common.ApiResponseWriter
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.application.security.IdempotencyKeyPolicy
import com.capstone.decision.infrastructure.brokerage.BrokerageWriteReplayPurpose
import com.capstone.decision.infrastructure.idempotency.IdempotencyLookup
import com.capstone.decision.infrastructure.idempotency.IdempotencyProperties
import com.capstone.decision.infrastructure.idempotency.IdempotencyService
import com.capstone.decision.infrastructure.web.BoundedContentCachingResponseWrapper
import com.capstone.decision.infrastructure.web.CachedBodyHttpServletRequest
import io.jsonwebtoken.JwtException
import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.springframework.beans.factory.ObjectProvider
import org.springframework.dao.DataAccessException
import org.springframework.http.HttpHeaders
import org.springframework.http.MediaType
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken
import org.springframework.security.core.authority.SimpleGrantedAuthority
import org.springframework.security.core.context.SecurityContextHolder
import org.springframework.util.AntPathMatcher
import org.springframework.web.filter.OncePerRequestFilter
import org.springframework.web.method.HandlerMethod
import org.springframework.web.servlet.mvc.method.annotation.RequestMappingHandlerMapping
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.HexFormat

// JWT 인증 뒤 authenticated write idempotency gate를 같은 보안 경계에서 처리한다.
class JwtAuthenticationFilter(
    private val jwtService: JwtService,
    private val idempotencyService: IdempotencyService,
    private val idempotencyProperties: IdempotencyProperties,
    private val responseWriter: ApiResponseWriter,
    private val handlerMappingProvider: ObjectProvider<RequestMappingHandlerMapping>,
) : OncePerRequestFilter() {
    private val pathMatcher = AntPathMatcher()

    override fun shouldNotFilter(request: HttpServletRequest): Boolean = request.method == "POST" && request.requestURI == LOGIN_PATH

    override fun doFilterInternal(
        request: HttpServletRequest,
        response: HttpServletResponse,
        filterChain: FilterChain,
    ) {
        if (!authenticate(request, response)) {
            return
        }
        val authentication = SecurityContextHolder.getContext().authentication
        if (authentication?.isAuthenticated == true && isIdempotentWritePath(request)) {
            handleIdempotentWrite(
                request = request,
                response = response,
                filterChain = filterChain,
                principal = authentication.principal as AppPrincipal,
            )
            return
        }
        filterChain.doFilter(request, response)
    }

    private fun authenticate(
        request: HttpServletRequest,
        response: HttpServletResponse,
    ): Boolean {
        val authorization = request.getHeader(HttpHeaders.AUTHORIZATION) ?: return true
        if (!authorization.startsWith(BEARER_PREFIX, ignoreCase = true)) {
            // Authorization header가 있으면 형식 오류도 인증 실패로 명확히 반환한다.
            responseWriter.writeError(request, response, ErrorCode.UNAUTHORIZED)
            return false
        }
        val token = authorization.substring(BEARER_PREFIX.length).trim()
        try {
            val principal = jwtService.parse(token)
            SecurityContextHolder.getContext().authentication =
                UsernamePasswordAuthenticationToken(
                    principal,
                    null,
                    listOf(SimpleGrantedAuthority("ROLE_${principal.role}")),
                )
        } catch (exception: JwtException) {
            // 파싱/서명 오류는 세부 원인을 숨겨 token probing 단서를 줄인다.
            SecurityContextHolder.clearContext()
            responseWriter.writeError(request, response, ErrorCode.UNAUTHORIZED)
            return false
        } catch (exception: IllegalArgumentException) {
            SecurityContextHolder.clearContext()
            responseWriter.writeError(request, response, ErrorCode.UNAUTHORIZED)
            return false
        } catch (exception: DataAccessException) {
            // actor source DB를 확인할 수 없을 때 token claim만 신뢰하지 않고 인증을 닫는다.
            SecurityContextHolder.clearContext()
            responseWriter.writeError(request, response, ErrorCode.UNAUTHORIZED)
            return false
        }
        return true
    }

    private fun handleIdempotentWrite(
        request: HttpServletRequest,
        response: HttpServletResponse,
        filterChain: FilterChain,
        principal: AppPrincipal,
    ) {
        val idempotencyKey = request.getHeader(IDEMPOTENCY_HEADER)?.takeIf { it.isNotBlank() }
        if (idempotencyKey == null) {
            // 쓰기 재시도 안전성을 보장해야 하므로 대상 path에서는 key 누락을 400으로 막는다.
            responseWriter.writeError(
                request = request,
                response = response,
                code = ErrorCode.VALIDATION_ERROR,
                details = mapOf(IDEMPOTENCY_HEADER to "Required for this write path."),
            )
            return
        }
        if (!isValidIdempotencyKey(idempotencyKey)) {
            responseWriter.writeError(
                request = request,
                response = response,
                code = ErrorCode.VALIDATION_ERROR,
                details = mapOf(IDEMPOTENCY_HEADER to "Must be a bounded ASCII identifier."),
            )
            return
        }

        // request body는 한 번 읽으면 사라지므로 hash 계산과 controller 전달을 모두 위해 캐시한다.
        val cachedRequest =
            request as? CachedBodyHttpServletRequest
                ?: CachedBodyHttpServletRequest(
                    request,
                    idempotencyProperties.maxRequestBodyBytes,
                )
        if (cachedRequest.cachedBody.size > idempotencyProperties.maxRequestBodyBytes) {
            responseWriter.writeError(request, response, ErrorCode.PAYLOAD_TOO_LARGE)
            return
        }
        val requestHash = requestHash(cachedRequest)
        val replayPurpose = replayPurpose(cachedRequest)
        when (
            val lookup =
                idempotencyService.acquire(
                    userId = principal.userId,
                    actorRole = principal.role,
                    securityVersion = principal.securityVersion,
                    idempotencyKey = idempotencyKey,
                    requestHash = requestHash,
                    purpose = replayPurpose,
                )
        ) {
            IdempotencyLookup.Conflict -> {
                // 같은 key로 다른 payload가 오면 중복 재시도가 아니라 위험한 충돌로 본다.
                responseWriter.writeError(
                    request = cachedRequest,
                    response = response,
                    code = ErrorCode.IDEMPOTENCY_CONFLICT,
                )
            }

            is IdempotencyLookup.Replay -> {
                // 동일 payload 재시도는 부작용 없이 최초 status/body를 그대로 돌려야 한다.
                response.status = lookup.status
                response.contentType = lookup.contentType
                response.characterEncoding = StandardCharsets.UTF_8.name()
                response.writer.write(lookup.body)
            }

            IdempotencyLookup.InProgress -> {
                responseWriter.writeError(
                    request = cachedRequest,
                    response = response,
                    code = ErrorCode.IDEMPOTENCY_IN_PROGRESS,
                )
            }

            IdempotencyLookup.CapacityExceeded -> {
                responseWriter.writeError(
                    request = cachedRequest,
                    response = response,
                    code = ErrorCode.RATE_LIMITED,
                )
            }

            is IdempotencyLookup.New -> {
                // controller 실행 결과를 저장해야 다음 동일 요청을 재실행하지 않을 수 있다.
                val responseWrapper =
                    BoundedContentCachingResponseWrapper(
                        response,
                        idempotencyProperties.maxResponseBodyBytes,
                    )
                filterChain.doFilter(cachedRequest, responseWrapper)
                var responseBodyBytes = responseWrapper.contentAsByteArray
                if (responseWrapper.overflowed) {
                    // side effect 이후 replay 기록을 버리면 재시도가 중복 실행되므로 안전한 오류를 대신 저장한다.
                    responseWrapper.reset()
                    responseWriter.writeError(
                        request = cachedRequest,
                        response = responseWrapper,
                        code = ErrorCode.CONFLICT,
                        details = mapOf("idempotency" to "Response exceeded replay safety limit."),
                    )
                    responseBodyBytes = responseWrapper.contentAsByteArray
                }
                if (responseWrapper.status in NON_REPLAYABLE_CLIENT_ERRORS) {
                    // 인가/라우팅/검증 실패는 부작용 결과가 아니므로 Redis 장기 점유 없이 owner claim을 반납한다.
                    idempotencyService.discard(
                        userId = principal.userId,
                        actorRole = principal.role,
                        securityVersion = principal.securityVersion,
                        idempotencyKey = idempotencyKey,
                        requestHash = requestHash,
                        claimToken = lookup.claimToken,
                        purpose = replayPurpose,
                    )
                    responseWrapper.copyBodyToResponse()
                    return
                }
                val responseBody = responseBodyBytes.toString(StandardCharsets.UTF_8)
                idempotencyService.store(
                    userId = principal.userId,
                    actorRole = principal.role,
                    securityVersion = principal.securityVersion,
                    idempotencyKey = idempotencyKey,
                    requestHash = requestHash,
                    claimToken = lookup.claimToken,
                    status = responseWrapper.status,
                    body = responseBody,
                    contentType = responseWrapper.contentType ?: MediaType.APPLICATION_JSON_VALUE,
                    purpose = replayPurpose,
                )
                responseWrapper.copyBodyToResponse()
            }
        }
    }

    private fun isIdempotentWritePath(request: HttpServletRequest): Boolean =
        request.method in WRITE_METHODS &&
            idempotencyProperties.paths.any { pathMatcher.match(it, request.requestURI) } &&
            handlerMappingProvider.getObject().getHandler(request)?.handler is HandlerMethod

    private fun isValidIdempotencyKey(value: String): Boolean = IdempotencyKeyPolicy.isValid(value, idempotencyProperties.maxKeyLength)

    private fun replayPurpose(request: HttpServletRequest): BrokerageWriteReplayPurpose =
        when {
            ORDER_CANCEL_PATH.matches(request.requestURI) -> BrokerageWriteReplayPurpose.ORDER_CANCEL
            FILL_APPLY_PATH.matches(request.requestURI) -> BrokerageWriteReplayPurpose.FILL_APPLY
            else -> BrokerageWriteReplayPurpose.GENERIC_FINANCE_WRITE
        }

    private fun requestHash(request: CachedBodyHttpServletRequest): String {
        val digest = MessageDigest.getInstance("SHA-256")
        // X-Request-Id는 재시도마다 달라질 수 있어 hash에서 제외하고 실제 요청 의미만 묶는다.
        digest.update(request.method.toByteArray(StandardCharsets.UTF_8))
        digest.update(0)
        digest.update(request.requestURI.toByteArray(StandardCharsets.UTF_8))
        digest.update(0)
        digest.update((request.queryString ?: "").toByteArray(StandardCharsets.UTF_8))
        digest.update(0)
        digest.update(request.cachedBody)
        return HexFormat.of().formatHex(digest.digest())
    }

    companion object {
        private const val BEARER_PREFIX = "Bearer "
        private const val LOGIN_PATH = "/api/v1/auth/login"
        private const val IDEMPOTENCY_HEADER = "X-Idempotency-Key"
        private val ORDER_CANCEL_PATH =
            Regex("^/api/v1/brokerage/orders/ord_(?:mock|paper)_[0-9a-f]{32}/cancel$")
        private val FILL_APPLY_PATH =
            Regex("^/api/v1/brokerage/orders/ord_(?:mock|paper)_[0-9a-f]{32}/reconcile$")
        private val WRITE_METHODS = setOf("POST", "PUT", "PATCH", "DELETE")
        private val NON_REPLAYABLE_CLIENT_ERRORS = setOf(400, 401, 403, 404, 405, 413, 422, 429)
    }
}
