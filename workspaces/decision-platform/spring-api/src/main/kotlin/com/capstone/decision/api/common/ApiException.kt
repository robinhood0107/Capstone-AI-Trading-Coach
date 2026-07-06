package com.capstone.decision.api.common

// 왜: 비즈니스 오류를 Spring 예외로 던져도 GlobalExceptionHandler에서 같은 envelope로 수렴시킨다.
class ApiException(
    val errorCode: ErrorCode,
    override val message: String = errorCode.defaultMessage,
    val details: Map<String, Any?> = emptyMap(),
) : RuntimeException(message)
