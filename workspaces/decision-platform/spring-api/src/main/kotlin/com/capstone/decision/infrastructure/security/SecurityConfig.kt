package com.capstone.decision.infrastructure.security

import com.capstone.decision.api.common.ApiResponseWriter
import com.capstone.decision.infrastructure.idempotency.IdempotencyProperties
import com.capstone.decision.infrastructure.idempotency.IdempotencyService
import com.capstone.decision.infrastructure.web.HttpRequestProperties
import com.capstone.decision.infrastructure.web.RequestBodyLimitFilter
import com.capstone.decision.infrastructure.web.RequestIdFilter
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
import org.springframework.security.web.SecurityFilterChain
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter
import org.springframework.web.cors.CorsConfiguration
import org.springframework.web.cors.CorsConfigurationSource
import org.springframework.web.cors.UrlBasedCorsConfigurationSource
import org.springframework.web.servlet.mvc.method.annotation.RequestMappingHandlerMapping

// S0.3 공통 규약에서 허용 경로, JWT 인증, CORS를 한 보안 체인으로 고정한다.
@Configuration
@EnableWebSecurity
@EnableMethodSecurity
@EnableConfigurationProperties(
    JwtProperties::class,
    DemoAccountProperties::class,
    IdempotencyProperties::class,
    HttpRequestProperties::class,
)
class SecurityConfig {
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
