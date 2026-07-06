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
            return body
        }
        val servletRequest = currentServletRequest() ?: return body
        if (!servletRequest.requestURI.startsWith("/api/")) {
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
