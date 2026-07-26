package com.capstone.decision.infrastructure.security

import com.capstone.decision.api.common.ApiResponseWriter
import com.capstone.decision.infrastructure.brokerage.BrokerageProperties
import com.capstone.decision.infrastructure.decision.DecisionProperties
import com.capstone.decision.infrastructure.grpc.BrokerageGrpcProperties
import com.capstone.decision.infrastructure.grpc.DecisionGrpcProperties
import com.capstone.decision.infrastructure.idempotency.IdempotencyProperties
import com.capstone.decision.infrastructure.idempotency.IdempotencyService
import com.capstone.decision.infrastructure.principle.PrincipleProperties
import com.capstone.decision.infrastructure.web.HttpRequestProperties
import com.capstone.decision.infrastructure.web.RequestBodyLimitFilter
import com.capstone.decision.infrastructure.web.RequestIdFilter
import org.flywaydb.core.api.migration.JavaMigration
import org.springframework.beans.factory.ObjectProvider
import org.springframework.beans.factory.annotation.Qualifier
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
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
    BrokerageGrpcProperties::class,
    DecisionGrpcProperties::class,
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
    ): AuthSecretSeparation {
        jwtProperties.validate()
        loginProperties.validate()
        principleProperties.validate()
        decisionProperties.validate()
        brokerageProperties.validate()
        val jwtSecret = jwtProperties.secret.toByteArray(StandardCharsets.UTF_8)
        val loginScopeKey = loginProperties.scopeHmacKey.toByteArray(StandardCharsets.UTF_8)
        val principleCursorKey = principleProperties.cursorHmacKey.toByteArray(StandardCharsets.UTF_8)
        val decisionScopeKey = decisionProperties.idempotencyScopeHmacKey.toByteArray(StandardCharsets.UTF_8)
        val brokerageScopeKey = brokerageProperties.idempotencyScopeHmacKey.toByteArray(StandardCharsets.UTF_8)
        val brokerageDatabaseCapability =
            brokerageProperties.databaseCapabilityToken.toByteArray(StandardCharsets.UTF_8)
        val credentialSeparationKey =
            DemoCredentialBundlePolicy.decodeSeparationKey(demoCredentialProperties.separationKey)
        return try {
            require(!MessageDigest.isEqual(jwtSecret, loginScopeKey)) {
                "JWT and login scope HMAC secrets must be different."
            }
            require(
                !MessageDigest.isEqual(credentialSeparationKey, jwtSecret) &&
                    !MessageDigest.isEqual(credentialSeparationKey, loginScopeKey) &&
                    !MessageDigest.isEqual(credentialSeparationKey, principleCursorKey) &&
                    !MessageDigest.isEqual(credentialSeparationKey, decisionScopeKey) &&
                    !MessageDigest.isEqual(credentialSeparationKey, brokerageScopeKey) &&
                    !MessageDigest.isEqual(credentialSeparationKey, brokerageDatabaseCapability) &&
                    !MessageDigest.isEqual(jwtSecret, principleCursorKey) &&
                    !MessageDigest.isEqual(jwtSecret, decisionScopeKey) &&
                    !MessageDigest.isEqual(jwtSecret, brokerageScopeKey) &&
                    !MessageDigest.isEqual(jwtSecret, brokerageDatabaseCapability) &&
                    !MessageDigest.isEqual(loginScopeKey, principleCursorKey) &&
                    !MessageDigest.isEqual(loginScopeKey, decisionScopeKey) &&
                    !MessageDigest.isEqual(loginScopeKey, brokerageScopeKey) &&
                    !MessageDigest.isEqual(loginScopeKey, brokerageDatabaseCapability) &&
                    !MessageDigest.isEqual(principleCursorKey, decisionScopeKey) &&
                    !MessageDigest.isEqual(principleCursorKey, brokerageScopeKey) &&
                    !MessageDigest.isEqual(principleCursorKey, brokerageDatabaseCapability) &&
                    !MessageDigest.isEqual(decisionScopeKey, brokerageScopeKey) &&
                    !MessageDigest.isEqual(decisionScopeKey, brokerageDatabaseCapability) &&
                    !MessageDigest.isEqual(brokerageScopeKey, brokerageDatabaseCapability),
            ) {
                "Authentication, Principle, Decision, Brokerage HMAC, and database capability secrets must be purpose-separated."
            }
            verifyBootstrapBundles(demoCredentialProperties, credentialSeparationKey)
            AuthSecretSeparation
        } finally {
            jwtSecret.fill(0)
            loginScopeKey.fill(0)
            principleCursorKey.fill(0)
            decisionScopeKey.fill(0)
            brokerageScopeKey.fill(0)
            brokerageDatabaseCapability.fill(0)
            credentialSeparationKey.fill(0)
        }
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
