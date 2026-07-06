package com.capstone.decision.infrastructure.security

import org.springframework.boot.context.properties.ConfigurationProperties

// 왜: demo 계정 비밀번호는 코드가 아니라 환경/설정으로만 주입해 secret 커밋 위험을 줄인다.
@ConfigurationProperties("app.demo")
data class DemoAccountProperties(
    var user: DemoAccountCredential = DemoAccountCredential(username = "demo-user"),
    var admin: DemoAccountCredential = DemoAccountCredential(username = "demo-admin"),
)

// 왜: USER/ADMIN을 같은 구조로 바인딩해 stage 1 인증 코드를 단순하게 유지한다.
data class DemoAccountCredential(
    var username: String = "",
    var password: String = "",
)
