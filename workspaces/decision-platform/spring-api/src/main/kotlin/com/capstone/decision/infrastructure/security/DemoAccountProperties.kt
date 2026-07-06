package com.capstone.decision.infrastructure.security

import org.springframework.boot.context.properties.ConfigurationProperties

@ConfigurationProperties("app.demo")
data class DemoAccountProperties(
    var user: DemoAccountCredential = DemoAccountCredential(username = "demo-user"),
    var admin: DemoAccountCredential = DemoAccountCredential(username = "demo-admin"),
)

data class DemoAccountCredential(
    var username: String = "",
    var password: String = "",
)
