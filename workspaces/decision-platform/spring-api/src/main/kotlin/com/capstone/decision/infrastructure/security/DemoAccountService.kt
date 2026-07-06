package com.capstone.decision.infrastructure.security

import org.springframework.stereotype.Service

// 왜: S0.3은 DB 사용자 테이블 없이 명세의 demo 계정만 인증 흐름을 검증한다.
@Service
class DemoAccountService(
    private val properties: DemoAccountProperties,
) {
    fun authenticate(
        username: String,
        password: String,
    ): DemoAccount? =
        // 왜: 비밀번호 비교는 demo 전용이며, 실제 사용자 저장소 도입 전 계약 smoke에만 쓴다.
        accounts().firstOrNull { account ->
            account.username == username && account.password == password
        }

    fun findByUsername(username: String): DemoAccount? = accounts().firstOrNull { account -> account.username == username }

    // 왜: username 기본값은 고정하되 password는 외부 주입을 강제해 테스트/로컬 실행을 분리한다.
    private fun accounts(): List<DemoAccount> =
        listOf(
            DemoAccount(
                userId = "demo-user",
                username = properties.user.username.ifBlank { "demo-user" },
                password = properties.user.password,
                role = DemoRole.USER,
            ),
            DemoAccount(
                userId = "demo-admin",
                username = properties.admin.username.ifBlank { "demo-admin" },
                password = properties.admin.password,
                role = DemoRole.ADMIN,
            ),
        )
}

// 왜: demo 인증 결과와 JWT claim 생성에 필요한 값만 담아 민감정보 범위를 좁힌다.
data class DemoAccount(
    val userId: String,
    val username: String,
    val password: String,
    val role: DemoRole,
)

// 왜: S0.3 권한 테스트는 USER와 ADMIN의 최소 역할 차이만 확인한다.
enum class DemoRole {
    USER,
    ADMIN,
}
