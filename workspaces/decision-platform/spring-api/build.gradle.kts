plugins {
    kotlin("jvm") version "2.4.0"
    kotlin("plugin.spring") version "2.4.0" // @Service 등 all-open
    kotlin("plugin.jpa") version "2.4.0" // 엔티티 no-arg 생성자
    id("org.springframework.boot") version "4.1.0"
    id("io.spring.dependency-management") version "1.1.7"
    id("org.jlleitschuh.gradle.ktlint") version "14.2.0" // 13.1.0+ Gradle 9, 14.0.1+ Gradle 9.1/Java 25 대응
}

group = "com.capstone"
version = "0.0.1-SNAPSHOT"

java {
    // toolchain: java/kotlin 컴파일 타깃을 한 곳에서 고정 (Gradle 공식 권장 — sourceCompatibility 단독은 Kotlin jvmTarget과 어긋날 수 있음)
    toolchain {
        languageVersion = JavaLanguageVersion.of(25)
    }
}

repositories {
    mavenCentral()
}

dependencies {
    implementation(kotlin("reflect")) // Spring이 Kotlin 클래스를 다루는 데 필요
    implementation("tools.jackson.module:jackson-module-kotlin") // Spring Boot 4/Jackson 3 Kotlin module
    implementation("org.springframework.boot:spring-boot-starter-webmvc")
    implementation("org.springframework.boot:spring-boot-starter-validation")
    implementation("org.springframework.boot:spring-boot-starter-data-jpa")
    implementation("org.springframework.boot:spring-boot-starter-data-redis") // Lettuce
    implementation("org.springframework.boot:spring-boot-starter-security")
    implementation("org.springframework.boot:spring-boot-starter-actuator")
    implementation("org.springframework.boot:spring-boot-starter-aspectj")
    implementation("org.springframework.boot:spring-boot-starter-kafka")
    implementation("org.flywaydb:flyway-core")
    implementation("org.flywaydb:flyway-database-postgresql")
    runtimeOnly("org.postgresql:postgresql")
    implementation("io.jsonwebtoken:jjwt-api:0.12.6")
    runtimeOnly("io.jsonwebtoken:jjwt-impl:0.12.6")
    runtimeOnly("io.jsonwebtoken:jjwt-gson:0.12.6")
    implementation("io.github.resilience4j:resilience4j-spring-boot4:2.4.0")
    implementation("net.javacrumbs.shedlock:shedlock-spring:7.7.0") // 7.x가 Spring 7.0/Boot 4.x 테스트 대상
    implementation("net.javacrumbs.shedlock:shedlock-provider-jdbc-template:7.7.0")
    implementation("org.springdoc:springdoc-openapi-starter-webmvc-ui:3.0.3")
    implementation("net.logstash.logback:logstash-logback-encoder:9.0") // 9.0부터 Jackson 3(Boot 4 정렬)
    runtimeOnly("io.micrometer:micrometer-registry-prometheus")
    // gRPC client는 contracts codegen 모듈 의존 (추후 추가)
    testImplementation("org.springframework.boot:spring-boot-starter-test")
    testImplementation("org.springframework.security:spring-security-test")
    testImplementation("io.kotest:kotest-assertions-core:5.9.1")
    testImplementation("io.mockk:mockk:1.14.11")
    testImplementation("org.springframework.boot:spring-boot-testcontainers")
    testImplementation(platform("org.testcontainers:testcontainers-bom:2.0.5"))
    testImplementation("org.testcontainers:testcontainers-junit-jupiter")
    testImplementation("org.testcontainers:testcontainers-postgresql")
    testImplementation("org.testcontainers:testcontainers-kafka")
    testImplementation("com.tngtech.archunit:archunit-junit5:1.4.2") // 1.4.1+ Java 25 classfile(major 69) 지원
}

kotlin {
    jvmToolchain(25)
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_25)
        freeCompilerArgs.addAll(
            "-Xjsr305=strict",
        )
    }
}

tasks.withType<Test> {
    useJUnitPlatform()
}
