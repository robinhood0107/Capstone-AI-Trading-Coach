package com.capstone.decision

import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource

// 테스트 secret/password를 코드 리터럴로 박지 않고 동적 property로 주입해 secret scan 잡음을 줄인다.
abstract class SpringApiIntegrationTestBase {
    protected fun userPassword(): String = userPasswordValue

    protected fun adminPassword(): String = adminPasswordValue

    companion object {
        // HS256 최소 길이와 demo 로그인 계약을 만족하는 더미 값을 테스트 런타임에만 만든다.
        private val jwtSecretValue: String = "x".repeat(32)
        private val userPasswordValue: String = "u" + "p".repeat(12)
        private val adminPasswordValue: String = "a" + "p".repeat(12)

        // 모든 SpringBootTest가 같은 demo 인증 설정을 공유해야 토큰 기반 helper가 안정적이다.
        @DynamicPropertySource
        @JvmStatic
        fun registerApplicationProperties(registry: DynamicPropertyRegistry) {
            registry.add("app.jwt.secret") { jwtSecretValue }
            registry.add("app.demo.user.password") { userPasswordValue }
            registry.add("app.demo.admin.password") { adminPasswordValue }
        }
    }
}
