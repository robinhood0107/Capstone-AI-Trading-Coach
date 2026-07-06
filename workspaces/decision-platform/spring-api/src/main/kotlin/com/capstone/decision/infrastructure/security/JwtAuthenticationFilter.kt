package com.capstone.decision.infrastructure.security

import com.capstone.decision.api.common.ApiResponseWriter
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.infrastructure.idempotency.IdempotencyLookup
import com.capstone.decision.infrastructure.idempotency.IdempotencyProperties
import com.capstone.decision.infrastructure.idempotency.IdempotencyService
import com.capstone.decision.infrastructure.web.CachedBodyHttpServletRequest
import io.jsonwebtoken.JwtException
import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.springframework.http.HttpHeaders
import org.springframework.http.MediaType
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken
import org.springframework.security.core.authority.SimpleGrantedAuthority
import org.springframework.security.core.context.SecurityContextHolder
import org.springframework.util.AntPathMatcher
import org.springframework.web.filter.OncePerRequestFilter
import org.springframework.web.util.ContentCachingResponseWrapper
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.HexFormat

// custom filter 수를 2개로 제한하면서 JWT 인증과 authenticated write idempotency gate를 함께 처리한다.
class JwtAuthenticationFilter(
    private val jwtService: JwtService,
    private val idempotencyService: IdempotencyService,
    private val idempotencyProperties: IdempotencyProperties,
    private val responseWriter: ApiResponseWriter,
) : OncePerRequestFilter() {
    private val pathMatcher = AntPathMatcher()

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
                    token,
                    listOf(SimpleGrantedAuthority("ROLE_${principal.role.name}")),
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

        // request body는 한 번 읽으면 사라지므로 hash 계산과 controller 전달을 모두 위해 캐시한다.
        val cachedRequest = CachedBodyHttpServletRequest(request)
        val requestHash = requestHash(cachedRequest)
        when (
            val lookup =
                idempotencyService.lookup(
                    userId = principal.userId,
                    idempotencyKey = idempotencyKey,
                    requestHash = requestHash,
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

            is IdempotencyLookup.New -> {
                // controller 실행 결과를 저장해야 다음 동일 요청을 재실행하지 않을 수 있다.
                val responseWrapper = ContentCachingResponseWrapper(response)
                filterChain.doFilter(cachedRequest, responseWrapper)
                val responseBody = responseWrapper.contentAsByteArray.toString(StandardCharsets.UTF_8)
                if (responseBody.isNotBlank()) {
                    idempotencyService.store(
                        userId = principal.userId,
                        idempotencyKey = idempotencyKey,
                        requestHash = requestHash,
                        status = responseWrapper.status,
                        body = responseBody,
                        contentType = responseWrapper.contentType ?: MediaType.APPLICATION_JSON_VALUE,
                    )
                }
                responseWrapper.copyBodyToResponse()
            }
        }
    }

    private fun isIdempotentWritePath(request: HttpServletRequest): Boolean =
        request.method in WRITE_METHODS &&
            idempotencyProperties.paths.any { pathMatcher.match(it, request.requestURI) }

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
        private const val IDEMPOTENCY_HEADER = "X-Idempotency-Key"
        private val WRITE_METHODS = setOf("POST", "PUT", "PATCH", "DELETE")
    }
}
