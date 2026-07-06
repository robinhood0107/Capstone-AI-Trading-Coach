package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.springframework.boot.autoconfigure.SpringBootApplication

class SpringApiApplicationTest {
    @Test
    fun `application class is a Spring Boot application`() {
        assertTrue(
            SpringApiApplication::class.java.isAnnotationPresent(SpringBootApplication::class.java),
        )
    }
}
