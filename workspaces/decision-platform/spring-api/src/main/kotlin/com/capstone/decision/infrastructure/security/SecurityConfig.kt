package com.capstone.decision.infrastructure.security

import com.capstone.decision.api.common.ApiResponseWriter
import com.capstone.decision.infrastructure.brokerage.BrokerageProperties
import com.capstone.decision.infrastructure.brokerage.PaperBrokerageProperties
import com.capstone.decision.infrastructure.decision.DecisionProperties
import com.capstone.decision.infrastructure.grpc.BrokerageGrpcProperties
import com.capstone.decision.infrastructure.grpc.DecisionGrpcProperties
import com.capstone.decision.infrastructure.grpc.RagGrpcProperties
import com.capstone.decision.infrastructure.idempotency.IdempotencyProperties
import com.capstone.decision.infrastructure.idempotency.IdempotencyService
import com.capstone.decision.infrastructure.principle.PrincipleProperties
import com.capstone.decision.infrastructure.rag.RagGuardHistoryProperties
import com.capstone.decision.infrastructure.web.HttpRequestProperties
import com.capstone.decision.infrastructure.web.RequestBodyLimitFilter
import com.capstone.decision.infrastructure.web.RequestIdFilter
import org.flywaydb.core.api.migration.JavaMigration
import org.springframework.beans.factory.ObjectProvider
import org.springframework.beans.factory.annotation.Qualifier
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.context.annotation.DependsOn
import org.springframework.http.HttpMethod
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity
import org.springframework.security.config.annotation.web.builders.HttpSecurity
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity
import org.springframework.security.config.http.SessionCreationPolicy
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder
import org.springframework.security.crypto.password.PasswordEncoder
import org.springframework.security.web.SecurityFilterChain
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter
import org.springframework.web.cors.CorsConfiguration
import org.springframework.web.cors.CorsConfigurationSource
import org.springframework.web.cors.UrlBasedCorsConfigurationSource
import org.springframework.web.servlet.mvc.method.annotation.RequestMappingHandlerMapping
import java.nio.charset.StandardCharsets
import java.security.MessageDigest

// S0.3 공통 규약에서 허용 경로, JWT 인증, CORS를 한 보안 체인으로 고정한다.
@Configuration
@EnableWebSecurity
@EnableMethodSecurity
@EnableConfigurationProperties(
    JwtProperties::class,
    LoginAttemptLimiterProperties::class,
    DemoCredentialBootstrapProperties::class,
    IdempotencyProperties::class,
    HttpRequestProperties::class,
    PrincipleProperties::class,
    DecisionProperties::class,
    BrokerageProperties::class,
    PaperBrokerageProperties::class,
    BrokerageGrpcProperties::class,
    DecisionGrpcProperties::class,
    RagGuardHistoryProperties::class,
    RagGrpcProperties::class,
)
class SecurityConfig {
    @Bean
    fun passwordEncoder(): PasswordEncoder = BCryptPasswordEncoder(12)

    @Bean
    fun authSecretSeparation(
        jwtProperties: JwtProperties,
        loginProperties: LoginAttemptLimiterProperties,
        demoCredentialProperties: DemoCredentialBootstrapProperties,
        principleProperties: PrincipleProperties,
        decisionProperties: DecisionProperties,
        brokerageProperties: BrokerageProperties,
        ragProperties: RagGuardHistoryProperties,
        decisionGrpcProperties: DecisionGrpcProperties,
        brokerageGrpcProperties: BrokerageGrpcProperties,
        ragGrpcProperties: RagGrpcProperties,
    ): AuthSecretSeparation {
        jwtProperties.validate()
        loginProperties.validate()
        principleProperties.validate()
        decisionProperties.validate()
        brokerageProperties.validate()
        ragProperties.validate()
        decisionGrpcProperties.validate()
        if (brokerageGrpcProperties.enabled) {
            brokerageGrpcProperties.validate()
        }
        if (ragGrpcProperties.enabled) {
            ragGrpcProperties.validate()
        }
        val secrets =
            linkedMapOf(
                "JWT" to jwtProperties.secret.toByteArray(StandardCharsets.UTF_8),
                "login scope" to loginProperties.scopeHmacKey.toByteArray(StandardCharsets.UTF_8),
                "Principle cursor" to principleProperties.cursorHmacKey.toByteArray(StandardCharsets.UTF_8),
                "Decision scope" to decisionProperties.idempotencyScopeHmacKey.toByteArray(StandardCharsets.UTF_8),
                "Brokerage scope" to brokerageProperties.idempotencyScopeHmacKey.toByteArray(StandardCharsets.UTF_8),
                "Brokerage database capability" to
                    brokerageProperties.databaseCapabilityToken.toByteArray(StandardCharsets.UTF_8),
                "demo credential separation" to
                    DemoCredentialBundlePolicy.decodeSeparationKey(demoCredentialProperties.separationKey),
                "RAG idempotency scope" to
                    ragProperties.idempotencyScopeHmacKey.toByteArray(StandardCharsets.UTF_8),
                "RAG request fingerprint" to
                    ragProperties.requestFingerprintHmacKey.toByteArray(StandardCharsets.UTF_8),
                "RAG provider usage" to
                    ragProperties.providerUsageHmacKey.toByteArray(StandardCharsets.UTF_8),
                "RAG rate limit" to
                    ragProperties.rateLimitHmacKey.toByteArray(StandardCharsets.UTF_8),
                "RAG history cursor" to
                    ragProperties.historyCursorHmacKey.toByteArray(StandardCharsets.UTF_8),
            )
        secrets["Decision/Python gRPC"] = decisionGrpcProperties.sharedSecret.toByteArray(StandardCharsets.UTF_8)
        if (brokerageGrpcProperties.enabled) {
            secrets["Brokerage gRPC"] = brokerageGrpcProperties.sharedSecret.toByteArray(StandardCharsets.UTF_8)
        }
        if (ragGrpcProperties.enabled) {
            secrets["RAG gRPC"] = ragGrpcProperties.sharedSecret.toByteArray(StandardCharsets.UTF_8)
        }
        return try {
            val entries = secrets.entries.toList()
            entries.indices.forEach { leftIndex ->
                ((leftIndex + 1) until entries.size).forEach { rightIndex ->
                    require(
                        !MessageDigest.isEqual(
                            entries[leftIndex].value,
                            entries[rightIndex].value,
                        ),
                    ) {
                        "${entries[leftIndex].key} and ${entries[rightIndex].key} secrets must be purpose-separated."
                    }
                }
            }
            verifyBootstrapBundles(
                demoCredentialProperties,
                requireNotNull(secrets["demo credential separation"]),
            )
            AuthSecretSeparation
        } finally {
            secrets.values.forEach { secret -> secret.fill(0) }
        }
    }

    /**
     * RAG gRPC를 켤 때만 dedicated wire secret을 검증해 RAG process credential이 다른 privileged credential에 재사용되지 않게 한다.
     */
    @Bean
    @DependsOn("authSecretSeparation")
    fun ragGrpcSecretSeparation(
        decisionGrpcProperties: DecisionGrpcProperties,
        ragGrpcProperties: RagGrpcProperties,
    ): RagGrpcSecretSeparation {
        if (!ragGrpcProperties.enabled) {
            return RagGrpcSecretSeparation
        }
        ragGrpcProperties.validatePurposeSeparation(decisionGrpcProperties)
        return RagGrpcSecretSeparation
    }

    @Bean
    fun s21ActorTrustMigration(properties: DemoCredentialBootstrapProperties): JavaMigration {
        val separationKey = DemoCredentialBundlePolicy.decodeSeparationKey(properties.separationKey)
        return try {
            val (userBundle, adminBundle) = verifyBootstrapBundles(properties, separationKey)
            V7__s2_1_actor_trust(userBundle, adminBundle)
        } finally {
            separationKey.fill(0)
        }
    }

    private fun verifyBootstrapBundles(
        properties: DemoCredentialBootstrapProperties,
        separationKey: ByteArray,
    ): Pair<VerifiedDemoCredentialBundle, VerifiedDemoCredentialBundle> {
        val userBundle =
            DemoCredentialBundlePolicy.verify(
                properties.userCredentialBundle,
                requireNotNull(DemoAccounts.byUserId("usr_demo_user")),
                separationKey,
            )
        val adminBundle =
            DemoCredentialBundlePolicy.verify(
                properties.adminCredentialBundle,
                requireNotNull(DemoAccounts.byUserId("usr_demo_admin")),
                separationKey,
            )
        DemoCredentialBundlePolicy.requireSeparated(userBundle, adminBundle)
        return userBundle to adminBundle
    }

    @Bean
    fun securityFilterChain(
        http: HttpSecurity,
        jwtService: JwtService,
        idempotencyService: IdempotencyService,
        idempotencyProperties: IdempotencyProperties,
        httpRequestProperties: HttpRequestProperties,
        responseWriter: ApiResponseWriter,
        @Qualifier("requestMappingHandlerMapping")
        handlerMappingProvider: ObjectProvider<RequestMappingHandlerMapping>,
    ): SecurityFilterChain {
        val requestIdFilter = RequestIdFilter()
        val requestBodyLimitFilter = RequestBodyLimitFilter(httpRequestProperties, responseWriter)
        val jwtAuthenticationFilter =
            // Redis idempotency를 별도 filter로 늘리지 않고 JWT 인증 뒤 write gate로 연결한다.
            JwtAuthenticationFilter(
                jwtService = jwtService,
                idempotencyService = idempotencyService,
                idempotencyProperties = idempotencyProperties,
                responseWriter = responseWriter,
                handlerMappingProvider = handlerMappingProvider,
            )
        return http
            // Bearer API에서는 브라우저 세션/폼 인증 상태를 만들지 않는다.
            .csrf { csrf -> csrf.disable() }
            .cors { cors -> cors.configurationSource(corsConfigurationSource()) }
            .httpBasic { httpBasic -> httpBasic.disable() }
            .formLogin { formLogin -> formLogin.disable() }
            .logout { logout -> logout.disable() }
            .sessionManagement { session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS) }
            .exceptionHandling { exceptionHandling ->
                exceptionHandling
                    .authenticationEntryPoint(ApiAuthenticationEntryPoint(responseWriter))
                    .accessDeniedHandler(ApiAccessDeniedHandler(responseWriter))
            }.authorizeHttpRequests { authorize ->
                authorize
                    .requestMatchers(HttpMethod.OPTIONS, "/**")
                    .permitAll()
                authorize
                    // liveness만 공개하고 metrics/info/prometheus는 운영정보이므로 ADMIN으로 제한한다.
                    .requestMatchers("/actuator/health")
                    .permitAll()
                authorize
                    .requestMatchers("/actuator/**")
                    .hasRole("ADMIN")
                authorize
                    // ADMIN route는 method security와 filter-chain 양쪽에서 기능 수준 권한을 고정한다.
                    .requestMatchers(HttpMethod.POST, "/api/v1/brokerage/orders/*/reconcile")
                    .hasRole("ADMIN")
                authorize
                    // 로그인과 개발 문서 endpoint는 토큰 발급 전에도 접근되어야 한다.
                    .requestMatchers(
                        "/swagger-ui/**",
                        "/swagger-ui.html",
                        "/v3/api-docs/**",
                        "/api/v1/auth/login",
                    ).permitAll()
                authorize
                    .anyRequest()
                    .authenticated()
            }.addFilterBefore(requestIdFilter, UsernamePasswordAuthenticationFilter::class.java)
            .addFilterAfter(requestBodyLimitFilter, RequestIdFilter::class.java)
            .addFilterAfter(jwtAuthenticationFilter, RequestBodyLimitFilter::class.java)
            .build()
    }

    @Bean
    fun corsConfigurationSource(): CorsConfigurationSource {
        // dashboard 로컬 개발 origin만 열어 S0.3 smoke와 최소 보안 경계를 함께 만족한다.
        val configuration =
            CorsConfiguration().apply {
                allowedOrigins = listOf("http://localhost:3000")
                allowedMethods = listOf("GET", "POST", "PUT", "PATCH", "DELETE")
                allowedHeaders = listOf("Authorization", "Content-Type", "X-Request-Id", "X-Idempotency-Key")
                exposedHeaders = listOf("X-Request-Id")
                allowCredentials = false
            }
        return UrlBasedCorsConfigurationSource().apply {
            registerCorsConfiguration("/**", configuration)
        }
    }
}

// 이 marker bean은 JWT, login limiter, credential evidence key 분리 검증이 startup에 완료됐음을 나타낸다.
object AuthSecretSeparation

// 이 marker bean은 enabled RAG gRPC가 Disclosure wire credential과 분리되었음을 나타낸다.
object RagGrpcSecretSeparation
