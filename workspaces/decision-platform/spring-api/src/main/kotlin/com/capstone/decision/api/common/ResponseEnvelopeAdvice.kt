package com.capstone.decision.api.common

import jakarta.servlet.http.HttpServletRequest
import org.springframework.core.MethodParameter
import org.springframework.http.MediaType
import org.springframework.http.converter.HttpMessageConverter
import org.springframework.http.server.ServerHttpRequest
import org.springframework.http.server.ServerHttpResponse
import org.springframework.stereotype.Controller
import org.springframework.web.bind.annotation.ControllerAdvice
import org.springframework.web.context.request.RequestContextHolder
import org.springframework.web.context.request.ServletRequestAttributes
import org.springframework.web.servlet.mvc.method.annotation.ResponseBodyAdvice

// controller마다 ApiResponse를 직접 감싸지 않아도 /api 응답 계약을 일관되게 지킨다.
@ControllerAdvice(annotations = [Controller::class])
class ResponseEnvelopeAdvice : ResponseBodyAdvice<Any> {
    override fun supports(
        returnType: MethodParameter,
        converterType: Class<out HttpMessageConverter<*>>,
    ): Boolean = true

    override fun beforeBodyWrite(
        body: Any?,
        returnType: MethodParameter,
        selectedContentType: MediaType,
        selectedConverterType: Class<out HttpMessageConverter<*>>,
        request: ServerHttpRequest,
        response: ServerHttpResponse,
    ): Any? {
        if (body is ApiResponse<*>) {
            // 오류 handler처럼 이미 envelope인 응답은 이중 포장을 피한다.
            return body
        }
        val servletRequest = currentServletRequest() ?: return body
        if (!servletRequest.requestURI.startsWith("/api/")) {
            // swagger-ui와 actuator 같은 비즈니스 API 밖 응답은 원래 포맷을 보존한다.
            return body
        }
        return ApiResponseFactory.success(
            requestId = RequestIds.currentOrCreate(servletRequest),
            data = body,
        )
    }

    private fun currentServletRequest(): HttpServletRequest? {
        val requestAttributes = RequestContextHolder.getRequestAttributes() as? ServletRequestAttributes
        return requestAttributes?.request
    }
}
