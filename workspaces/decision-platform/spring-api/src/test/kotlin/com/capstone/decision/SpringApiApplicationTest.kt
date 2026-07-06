package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.springframework.boot.autoconfigure.SpringBootApplication

// 왜: application class의 루트 패키지 위치와 Boot annotation이 skeleton의 스캔 범위를 결정한다.
class SpringApiApplicationTest {
    // 왜: annotation이 빠지면 모든 controller/security bean이 CI에서 조용히 스캔되지 않을 수 있다.
    @Test
    fun `application class is a Spring Boot application`() {
        assertTrue(
            SpringApiApplication::class.java.isAnnotationPresent(SpringBootApplication::class.java),
        )
    }
}
