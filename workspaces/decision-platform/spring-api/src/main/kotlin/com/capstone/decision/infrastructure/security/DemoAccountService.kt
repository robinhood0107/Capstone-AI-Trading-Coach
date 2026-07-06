package com.capstone.decision.infrastructure.security

import org.springframework.stereotype.Service

@Service
class DemoAccountService(
    private val properties: DemoAccountProperties,
) {
    fun authenticate(
        username: String,
        password: String,
    ): DemoAccount? =
        accounts().firstOrNull { account ->
            account.username == username && account.password == password
        }

    fun findByUsername(username: String): DemoAccount? = accounts().firstOrNull { account -> account.username == username }

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

data class DemoAccount(
    val userId: String,
    val username: String,
    val password: String,
    val role: DemoRole,
)

enum class DemoRole {
    USER,
    ADMIN,
}
