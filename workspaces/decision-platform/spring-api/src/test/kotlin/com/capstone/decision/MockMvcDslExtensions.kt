package com.capstone.decision

import org.springframework.test.web.servlet.MockHttpServletRequestDsl

internal fun MockHttpServletRequestDsl.bearer(token: String) {
    header("Authorization", "Bearer $token")
}
