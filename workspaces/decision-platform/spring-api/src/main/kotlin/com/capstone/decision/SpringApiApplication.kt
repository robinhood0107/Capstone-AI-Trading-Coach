package com.capstone.decision

import org.springframework.boot.SpringApplication
import org.springframework.boot.autoconfigure.SpringBootApplication

// Spring Boot 진입점을 루트 패키지에 두어 api/application/domain/infrastructure를 한 번에 스캔한다.
@SpringBootApplication
class SpringApiApplication

fun main(args: Array<String>) {
    SpringApplication.run(arrayOf(SpringApiApplication::class.java), args)
}
