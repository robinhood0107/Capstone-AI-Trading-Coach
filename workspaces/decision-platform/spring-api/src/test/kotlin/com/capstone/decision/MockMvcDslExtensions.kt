package com.capstone.decision

import org.springframework.test.web.servlet.MockHttpServletRequestDsl

// 테스트마다 Authorization header 문자열을 반복하지 않아 bearer 형식 실수를 줄인다.
internal fun MockHttpServletRequestDsl.bearer(token: String) {
    header("Authorization", "Bearer $token")
}
