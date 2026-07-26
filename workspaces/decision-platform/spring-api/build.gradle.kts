import com.google.protobuf.gradle.id
import org.gradle.language.jvm.tasks.ProcessResources

buildscript {
    configurations.classpath {
        // Spring Boot buildpack 전이 의존성에서 긴 입력 재귀 DoS 수정 버전을 강제한다.
        resolutionStrategy.force("org.apache.commons:commons-lang3:3.20.0")
    }
}

plugins {
    kotlin("jvm") version "2.4.0"
    kotlin("plugin.spring") version "2.4.0" // @Service 등 all-open
    kotlin("plugin.jpa") version "2.4.0" // 엔티티 no-arg 생성자
    id("org.springframework.boot") version "4.1.0"
    id("org.springdoc.openapi-gradle-plugin") version "1.9.0"
    id("io.spring.dependency-management") version "1.1.7"
    id("org.jlleitschuh.gradle.ktlint") version "14.2.0" // 13.1.0+ Gradle 9, 14.0.1+ Gradle 9.1/Java 25 대응
    id("com.google.protobuf") version "0.10.0"
}

group = "com.capstone"
version = "0.0.1-SNAPSHOT"

springBoot {
    // operator-only JavaExec main이 늘어나도 실행 가능한 서버 artifact의 진입점은 하나로 고정한다.
    mainClass.set("com.capstone.decision.SpringApiApplicationKt")
}

java {
    // toolchain: java/kotlin 컴파일 타깃을 한 곳에서 고정 (Gradle 공식 권장 — sourceCompatibility 단독은 Kotlin jvmTarget과 어긋날 수 있음)
    toolchain {
        languageVersion = JavaLanguageVersion.of(25)
    }
}

repositories {
    mavenCentral()
}

dependencyManagement {
    imports {
        mavenBom("io.grpc:grpc-bom:1.81.0")
    }
    dependencies {
        // Boot BOM의 다음 patch 반영 전에도 공개 취약점 수정 버전을 우선한다.
        dependency("com.fasterxml.jackson.core:jackson-databind:2.21.5")
        dependency("ch.qos.logback:logback-core:1.5.35")
        dependency("ch.qos.logback:logback-classic:1.5.35")
    }
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
    implementation("org.springframework.boot:spring-boot-starter-flyway")
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
    implementation("io.grpc:grpc-protobuf")
    implementation("io.grpc:grpc-stub")
    implementation("io.grpc:grpc-netty-shaded")
    implementation("com.google.protobuf:protobuf-java:4.35.0")
    compileOnly("javax.annotation:javax.annotation-api:1.3.2")
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

sourceSets {
    main {
        proto {
            srcDir("../../../contracts/proto")
            include("disclosure_observation.proto")
            include("brokerage.proto")
        }
    }
}

protobuf {
    protoc {
        artifact = "com.google.protobuf:protoc:4.35.0"
    }
    plugins {
        named("grpc") {
            artifact = "io.grpc:protoc-gen-grpc-java:1.81.0"
        }
    }
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

tasks.named<ProcessResources>("processResources") {
    // runtime/OpenAPI가 사람이 복사한 두 번째 matrix가 아니라 canonical catalog bytes를 그대로 읽는다.
    from(layout.projectDirectory.file("../../../contracts/catalogs/s2-1-principle-contract.v1.json")) {
        into("contracts")
    }
    // S2.2 runtime은 별도 복제본 없이 승인된 14-rule catalog bytes를 classpath에서 읽는다.
    from(layout.projectDirectory.file("../../../contracts/catalogs/s2-2-system-rule-catalog.v1.json")) {
        into("contracts")
    }
    // S2.3 OpenAPI extension과 component schema는 generator가 잠근 exact bytes만 사용한다.
    from(layout.projectDirectory.file("../../../contracts/catalogs/s2-3-decision-contract.v1.json")) {
        into("contracts")
    }
    from(layout.projectDirectory.file("../../../contracts/schemas/s2-3-evaluate-order-request.schema.json")) {
        into("contracts")
    }
    from(layout.projectDirectory.file("../../../contracts/schemas/s2-3-decision-response.schema.json")) {
        into("contracts")
    }
    // S2.4 OpenAPI도 승인된 JSON Schema bytes를 별도 DTO 추론 없이 component로 사용한다.
    from(layout.projectDirectory.file("../../../contracts/schemas/s2-4-kill-switch-request.schema.json")) {
        into("contracts")
    }
    from(layout.projectDirectory.file("../../../contracts/schemas/s2-4-kill-switch-state.schema.json")) {
        into("contracts")
    }
    from(layout.projectDirectory.file("../../../contracts/schemas/s2-4-risk-portfolio.schema.json")) {
        into("contracts")
    }
    // S3.1 Brokerage Mock도 canonical JSON Schema bytes를 OpenAPI component로 직접 노출한다.
    from(layout.projectDirectory.file("../../../contracts/schemas/s3-1-mock-order-request.schema.json")) {
        into("contracts")
    }
    from(layout.projectDirectory.file("../../../contracts/schemas/s3-1-mock-order-response.schema.json")) {
        into("contracts")
    }
    from(layout.projectDirectory.file("../../../contracts/schemas/s3-1-mock-order-detail.schema.json")) {
        into("contracts")
    }
    from(layout.projectDirectory.file("../../../contracts/schemas/s3-1-mock-balance.schema.json")) {
        into("contracts")
    }
    from(layout.projectDirectory.file("../../../contracts/schemas/s3-1-mock-buyable.schema.json")) {
        into("contracts")
    }
}

tasks.named<ProcessResources>("processTestResources") {
    // JVM canonicalizer가 Python generator의 exact hash vector를 직접 소비해 양쪽 byte parity를 검증한다.
    from(layout.projectDirectory.file("../../../contracts/examples/s2-2-hash-vector.valid.json")) {
        into("contracts")
    }
}

val verifyS22CatalogResource by tasks.registering {
    group = "verification"
    description = "S2.2 canonical catalog와 classpath resource의 byte equality를 검증한다."
    dependsOn(tasks.named("processResources"))

    doLast {
        val source =
            layout.projectDirectory
                .file("../../../contracts/catalogs/s2-2-system-rule-catalog.v1.json")
                .asFile
        val copied =
            layout.buildDirectory
                .file("resources/main/contracts/s2-2-system-rule-catalog.v1.json")
                .get()
                .asFile
        check(copied.isFile && source.readBytes().contentEquals(copied.readBytes())) {
            "S2.2 catalog classpath resource must be an exact canonical byte copy."
        }
    }
}

val verifyS23ContractResources by tasks.registering {
    group = "verification"
    description = "S2.3 catalog와 OpenAPI component resource의 exact byte equality를 검증한다."
    dependsOn(tasks.named("processResources"))

    doLast {
        listOf(
            "catalogs/s2-3-decision-contract.v1.json" to "s2-3-decision-contract.v1.json",
            "schemas/s2-3-evaluate-order-request.schema.json" to "s2-3-evaluate-order-request.schema.json",
            "schemas/s2-3-decision-response.schema.json" to "s2-3-decision-response.schema.json",
        ).forEach { (sourceRelative, copiedName) ->
            val source =
                layout.projectDirectory
                    .file("../../../contracts/$sourceRelative")
                    .asFile
            val copied =
                layout.buildDirectory
                    .file("resources/main/contracts/$copiedName")
                    .get()
                    .asFile
            check(copied.isFile && source.readBytes().contentEquals(copied.readBytes())) {
                "S2.3 contract resource $copiedName must be an exact canonical byte copy."
            }
        }
    }
}

val verifyS24ContractResources by tasks.registering {
    group = "verification"
    description = "S2.4 Risk/Kill Switch OpenAPI schema의 exact byte equality를 검증한다."
    dependsOn(tasks.named("processResources"))

    doLast {
        listOf(
            "s2-4-kill-switch-request.schema.json",
            "s2-4-kill-switch-state.schema.json",
            "s2-4-risk-portfolio.schema.json",
        ).forEach { fileName ->
            val source =
                layout.projectDirectory
                    .file("../../../contracts/schemas/$fileName")
                    .asFile
            val copied =
                layout.buildDirectory
                    .file("resources/main/contracts/$fileName")
                    .get()
                    .asFile
            check(copied.isFile && source.readBytes().contentEquals(copied.readBytes())) {
                "S2.4 contract resource $fileName must be an exact canonical byte copy."
            }
        }
    }
}

val verifyS31ContractResources by tasks.registering {
    group = "verification"
    description = "S3.1 Brokerage Mock OpenAPI schema의 exact byte equality를 검증한다."
    dependsOn(tasks.named("processResources"))

    doLast {
        listOf(
            "s3-1-mock-order-request.schema.json",
            "s3-1-mock-order-response.schema.json",
            "s3-1-mock-order-detail.schema.json",
            "s3-1-mock-balance.schema.json",
            "s3-1-mock-buyable.schema.json",
        ).forEach { fileName ->
            val source =
                layout.projectDirectory
                    .file("../../../contracts/schemas/$fileName")
                    .asFile
            val copied =
                layout.buildDirectory
                    .file("resources/main/contracts/$fileName")
                    .get()
                    .asFile
            check(copied.isFile && source.readBytes().contentEquals(copied.readBytes())) {
                "S3.1 contract resource $fileName must be an exact canonical byte copy."
            }
        }
    }
}

openApi {
    apiDocsUrl.set("http://127.0.0.1:18080/v3/api-docs")
    outputDir.set(layout.buildDirectory)
    outputFileName.set("openapi.json")
    waitTimeInSeconds.set(90)
    customBootRun {
        args.set(listOf("--spring.profiles.active=openapi", "--server.port=18080"))
    }
}

val cleanOpenApiOutput by tasks.registering(Delete::class) {
    group = "verification"
    description = "이전 generated OpenAPI가 새 실행을 통과시키지 못하도록 exact output만 삭제한다."
    delete(layout.buildDirectory.file("openapi.json"))
}

tasks.named("generateOpenApiDocs") {
    dependsOn(cleanOpenApiOutput)
}

tasks.register<JavaExec>("prepareOpenApiFixtureEnv") {
    group = "verification"
    description = "provider key 없이 격리 OpenAPI boot용 0600 credential bundle 환경을 생성한다."
    dependsOn(tasks.named("testClasses"))
    classpath = sourceSets["test"].runtimeClasspath
    mainClass.set("com.capstone.decision.OpenApiFixtureEnvironmentWriter")
    workingDir = projectDir
    argumentProviders.add(
        CommandLineArgumentProvider {
            listOf(
                layout.buildDirectory
                    .file("openapi-fixture/openapi.env")
                    .get()
                    .asFile.absolutePath,
            )
        },
    )
    // 매 실행 새 credential을 만들며 secret-bearing output은 cache/up-to-date state로 보존하지 않는다.
    doNotTrackState("OpenAPI fixture credentials are intentionally regenerated.")
}

val cleanAuthCutoverEvidence by tasks.registering(Delete::class) {
    group = "operations"
    description = "로컬 auth cutover 사전 증거 파일만 삭제한다."
    delete(layout.buildDirectory.file("auth-cutover/pre-cutover.json"))
}

tasks.register<JavaExec>("rotateDemoCredential") {
    group = "operations"
    description = "환경변수로 받은 attested demo credential bundle을 migration role transaction에서 회전한다."
    dependsOn(tasks.named("classes"))
    classpath = sourceSets["main"].runtimeClasspath
    mainClass.set("com.capstone.decision.infrastructure.security.DemoCredentialRotation")
    outputs.upToDateWhen { false }
}

tasks.register<JavaExec>("authPreCutoverCapture") {
    group = "operations"
    description = "배포 전 기존 JWT의 authenticated health 200을 digest evidence로 기록한다."
    dependsOn(tasks.named("classes"))
    mustRunAfter(cleanAuthCutoverEvidence)
    classpath = sourceSets["main"].runtimeClasspath
    mainClass.set("com.capstone.decision.infrastructure.security.AuthCutoverSmoke")
    args("capture")
    outputs.upToDateWhen { false }
}

tasks.register<JavaExec>("authCutoverSmoke") {
    group = "operations"
    description = "배포 후 같은 기존 JWT 401과 새 USER/ADMIN JWT health 200을 검증한다."
    dependsOn(tasks.named("classes"))
    classpath = sourceSets["main"].runtimeClasspath
    mainClass.set("com.capstone.decision.infrastructure.security.AuthCutoverSmoke")
    args("verify")
    outputs.upToDateWhen { false }
}

val verifySecurityDependencyVersions by tasks.registering {
    group = "verification"
    description = "OSV에서 확인한 최소 보안 버전이 runtime/build classpath에 선택됐는지 검증한다."

    doLast {
        fun selectedVersion(
            configuration: Configuration,
            group: String,
            module: String,
        ): String =
            configuration.resolvedConfiguration.resolvedArtifacts
                .single { it.moduleVersion.id.group == group && it.name == module }
                .moduleVersion.id.version

        val runtime = configurations.runtimeClasspath.get()
        check(selectedVersion(runtime, "ch.qos.logback", "logback-core") == "1.5.35") {
            "logback-core must include the GHSA-jhq6-gfmj-v8fx fix"
        }
        check(selectedVersion(runtime, "com.fasterxml.jackson.core", "jackson-databind") == "2.21.5") {
            "jackson-databind must include the GHSA-5jmj-h7xm-6q6v fix"
        }

        val buildClasspath = buildscript.configurations.getByName("classpath")
        check(selectedVersion(buildClasspath, "org.apache.commons", "commons-lang3") == "3.20.0") {
            "commons-lang3 must include the GHSA-j288-q9x7-2f5v fix"
        }
    }
}

tasks.named("check") {
    dependsOn(verifySecurityDependencyVersions)
    dependsOn(verifyS22CatalogResource)
    dependsOn(verifyS23ContractResources)
    dependsOn(verifyS24ContractResources)
    dependsOn(verifyS31ContractResources)
}
