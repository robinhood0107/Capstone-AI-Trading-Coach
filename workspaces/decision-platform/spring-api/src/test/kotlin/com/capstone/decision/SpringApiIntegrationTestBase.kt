package com.capstone.decision

import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource

abstract class SpringApiIntegrationTestBase {
    protected fun userPassword(): String = userPasswordValue

    companion object {
        private val jwtSecretValue: String = "x".repeat(32)
        private val userPasswordValue: String = "u" + "p".repeat(12)
        private val adminPasswordValue: String = "a" + "p".repeat(12)

        @DynamicPropertySource
        @JvmStatic
        fun registerApplicationProperties(registry: DynamicPropertyRegistry) {
            registry.add("app.jwt.secret") { jwtSecretValue }
            registry.add("app.demo.user.password") { userPasswordValue }
            registry.add("app.demo.admin.password") { adminPasswordValue }
        }
    }
}
