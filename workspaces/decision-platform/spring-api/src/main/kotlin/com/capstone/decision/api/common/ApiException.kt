package com.capstone.decision.api.common

class ApiException(
    val errorCode: ErrorCode,
    override val message: String = errorCode.defaultMessage,
    val details: Map<String, Any?> = emptyMap(),
) : RuntimeException(message)
