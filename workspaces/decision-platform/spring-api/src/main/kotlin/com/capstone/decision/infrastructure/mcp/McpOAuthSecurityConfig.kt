package com.capstone.decision.infrastructure.mcp

import com.capstone.decision.infrastructure.security.UserSecurityRepository
import com.nimbusds.jose.jwk.ECKey
import com.nimbusds.jose.jwk.JWKSet
import com.nimbusds.jose.jwk.source.ImmutableJWKSet
import com.nimbusds.jose.jwk.source.JWKSource
import com.nimbusds.jose.proc.SecurityContext
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.core.annotation.Order
import org.springframework.http.MediaType
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.security.config.Customizer
import org.springframework.security.config.annotation.web.builders.HttpSecurity
import org.springframework.security.config.annotation.web.configuration.OAuth2AuthorizationServerConfiguration
import org.springframework.security.config.http.SessionCreationPolicy
import org.springframework.security.core.userdetails.User
import org.springframework.security.core.userdetails.UserDetailsService
import org.springframework.security.oauth2.core.AuthorizationGrantType
import org.springframework.security.oauth2.core.ClientAuthenticationMethod
import org.springframework.security.oauth2.core.OAuth2Error
import org.springframework.security.oauth2.core.OAuth2TokenValidator
import org.springframework.security.oauth2.core.OAuth2TokenValidatorResult
import org.springframework.security.oauth2.jwt.Jwt
import org.springframework.security.oauth2.jwt.JwtDecoder
import org.springframework.security.oauth2.server.authorization.OAuth2AuthorizationService
import org.springframework.security.oauth2.server.authorization.OAuth2TokenType
import org.springframework.security.oauth2.server.authorization.client.InMemoryRegisteredClientRepository
import org.springframework.security.oauth2.server.authorization.client.RegisteredClient
import org.springframework.security.oauth2.server.authorization.client.RegisteredClientRepository
import org.springframework.security.oauth2.server.authorization.settings.AuthorizationServerSettings
import org.springframework.security.oauth2.server.authorization.settings.ClientSettings
import org.springframework.security.oauth2.server.authorization.settings.TokenSettings
import org.springframework.security.oauth2.server.authorization.token.JwtEncodingContext
import org.springframework.security.oauth2.server.authorization.token.OAuth2TokenCustomizer
import org.springframework.security.oauth2.server.authorization.web.OAuth2AuthorizationEndpointFilter
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationConverter
import org.springframework.security.web.SecurityFilterChain
import org.springframework.security.web.authentication.LoginUrlAuthenticationEntryPoint
import org.springframework.security.web.util.matcher.MediaTypeRequestMatcher
import tools.jackson.databind.json.JsonMapper
import java.nio.file.Files
import java.security.MessageDigest
import java.time.Duration
import java.util.HexFormat
import java.util.UUID

@Configuration
@ConditionalOnProperty(name = ["app.s4-9.mcp-oauth.enabled"], havingValue = "true")
@EnableConfigurationProperties(McpOAuthProperties::class)
class McpOAuthSecurityConfig {
    @Bean
    @Order(1)
    fun authorizationServerSecurityFilterChain(
        http: HttpSecurity,
        properties: McpOAuthProperties,
    ): SecurityFilterChain {
        http
            .oauth2AuthorizationServer { authorizationServer ->
                http.securityMatcher(authorizationServer.endpointsMatcher)
            }.authorizeHttpRequests { it.anyRequest().authenticated() }
            .exceptionHandling {
                it.defaultAuthenticationEntryPointFor(
                    LoginUrlAuthenticationEntryPoint("/login"),
                    MediaTypeRequestMatcher(MediaType.TEXT_HTML),
                )
            }.addFilterBefore(McpResourceIndicatorFilter(properties.resourceUri), OAuth2AuthorizationEndpointFilter::class.java)
        return http.build()
    }

    @Bean
    @Order(2)
    fun mcpResourceSecurityFilterChain(
        http: HttpSecurity,
        jwtDecoder: JwtDecoder,
    ): SecurityFilterChain {
        val converter = JwtAuthenticationConverter()
        return http
            .securityMatcher("/mcp", "/mcp/**")
            .csrf { it.disable() }
            .sessionManagement { it.disable() }
            .authorizeHttpRequests { it.anyRequest().authenticated() }
            .oauth2ResourceServer { resource ->
                resource.jwt { jwt -> jwt.decoder(jwtDecoder).jwtAuthenticationConverter(converter) }
            }.build()
    }

    @Bean
    @Order(3)
    fun mcpLoginSecurityFilterChain(http: HttpSecurity): SecurityFilterChain =
        http
            .securityMatcher("/login")
            .authorizeHttpRequests { it.anyRequest().permitAll() }
            .sessionManagement { it.sessionCreationPolicy(SessionCreationPolicy.IF_REQUIRED) }
            .formLogin(Customizer.withDefaults())
            .build()

    @Bean
    fun mcpUserDetailsService(users: UserSecurityRepository): UserDetailsService =
        UserDetailsService { username ->
            val row =
                users.findDemoCredentials().singleOrNull { it.username == username && it.status == "ACTIVE" }
                    ?: throw org.springframework.security.core.userdetails
                        .UsernameNotFoundException("Unknown user")
            User
                .withUsername(row.username)
                .password(row.passwordHash)
                .roles(row.role.name)
                .build()
        }

    @Bean
    fun registeredClientRepository(
        properties: McpOAuthProperties,
        jdbc: NamedParameterJdbcTemplate,
    ): RegisteredClientRepository {
        properties.validateEnabled()
        val root = strictMapper().readTree(readSecret(properties.clientAllowlistPath))
        require(root != null && root.isObject && root.properties().map { it.key }.toSet() == setOf("clients"))
        val clients = root.get("clients")
        require(clients != null && clients.isArray && clients.size() in 1..16)
        return InMemoryRegisteredClientRepository(
            clients
                .values()
                .asSequence()
                .map { node ->
                    require(node.isObject)
                    val clientId = node.get("clientId").stringValue()
                    val clientName = node.get("clientName").stringValue()
                    val kind = node.get("clientKind")?.stringValue() ?: "STATIC_ALLOWLIST"
                    require(kind in setOf("STATIC_ALLOWLIST", "CIMD_VERIFIED"))
                    val redirectUris =
                        node
                            .get("redirectUris")
                            .values()
                            .asSequence()
                            .map { it.stringValue() }
                            .toList()
                    val scopes =
                        node
                            .get("scopes")
                            .values()
                            .asSequence()
                            .map { it.stringValue() }
                            .toList()
                    val metadataHash = metadataSha256(clientId, clientName, kind, redirectUris, scopes)
                    val builder =
                        RegisteredClient
                            .withId(UUID.nameUUIDFromBytes(clientId.toByteArray()).toString())
                            .clientId(clientId)
                            .clientName(clientName)
                            .authorizationGrantType(AuthorizationGrantType.AUTHORIZATION_CODE)
                            .authorizationGrantType(AuthorizationGrantType.REFRESH_TOKEN)
                    if (kind == "STATIC_ALLOWLIST") {
                        builder.clientSecret(node.get("clientSecretBcrypt").stringValue())
                        builder.clientAuthenticationMethod(ClientAuthenticationMethod.CLIENT_SECRET_BASIC)
                        require(clientId.matches(Regex("^mcp_[a-z0-9][a-z0-9._-]{2,95}$")))
                    } else {
                        builder.clientAuthenticationMethod(ClientAuthenticationMethod.NONE)
                        require(clientId.startsWith("https://"))
                        require(node.get("clientSecretBcrypt") == null)
                    }
                    redirectUris.forEach(builder::redirectUri)
                    scopes.forEach(builder::scope)
                    require(
                        jdbc.queryForObject(
                            """
                            SELECT public.sync_s4_9_mcp_oauth_client(
                              :clientId, :clientName, :metadataHash,
                              CAST(:redirectUris AS text[]), CAST(:scopes AS text[]), :kind
                            ) IS NULL
                            """.trimIndent(),
                            mapOf(
                                "clientId" to clientId,
                                "clientName" to clientName,
                                "metadataHash" to metadataHash,
                                "redirectUris" to redirectUris.toTypedArray(),
                                "scopes" to scopes.toTypedArray(),
                                "kind" to kind,
                            ),
                            Boolean::class.java,
                        ) == true,
                    )
                    builder
                        .clientSettings(
                            ClientSettings
                                .builder()
                                .requireProofKey(true)
                                .requireAuthorizationConsent(true)
                                .build(),
                        ).tokenSettings(
                            TokenSettings
                                .builder()
                                .accessTokenTimeToLive(Duration.ofMinutes(15))
                                .refreshTokenTimeToLive(Duration.ofDays(7))
                                .reuseRefreshTokens(false)
                                .build(),
                        ).build()
                }.toList(),
        )
    }

    @Bean
    fun mcpJwkSource(properties: McpOAuthProperties): JWKSource<SecurityContext> {
        properties.validateEnabled()
        val ecKey = ECKey.parse(readSecret(properties.signingJwkPath))
        require(ecKey.isPrivate && ecKey.curve.name == "P-256" && !ecKey.keyID.isNullOrBlank())
        return ImmutableJWKSet(JWKSet(ecKey))
    }

    @Bean
    fun mcpJwtDecoder(
        source: JWKSource<SecurityContext>,
        properties: McpOAuthProperties,
        users: UserSecurityRepository,
    ): JwtDecoder {
        val decoder = OAuth2AuthorizationServerConfiguration.jwtDecoder(source) as org.springframework.security.oauth2.jwt.NimbusJwtDecoder
        val issuer =
            org.springframework.security.oauth2.jwt.JwtValidators
                .createDefaultWithIssuer(properties.issuer)
        val boundary = OAuth2TokenValidator<Jwt> { jwt -> validateActor(jwt, properties, users) }
        decoder.setJwtValidator(
            org.springframework.security.oauth2.core
                .DelegatingOAuth2TokenValidator(issuer, boundary),
        )
        return decoder
    }

    @Bean
    fun mcpTokenCustomizer(
        users: UserSecurityRepository,
        properties: McpOAuthProperties,
    ): OAuth2TokenCustomizer<JwtEncodingContext> =
        OAuth2TokenCustomizer { context ->
            if (context.tokenType == OAuth2TokenType.ACCESS_TOKEN) {
                val principal = requireNotNull(context.getPrincipal<org.springframework.security.core.Authentication>())
                val row = users.findDemoCredentials().single { it.username == principal.name && it.status == "ACTIVE" }
                context.claims.subject(row.userId)
                context.claims.audience(listOf(properties.resourceUri))
                context.claims.claim("securityVersion", row.securityVersion)
                context.claims.claim("client_id", context.registeredClient.clientId)
            }
        }

    @Bean
    fun authorizationServerSettings(properties: McpOAuthProperties): AuthorizationServerSettings =
        AuthorizationServerSettings.builder().issuer(properties.issuer).build()

    @Bean
    fun mcpAuthorizationService(
        clients: RegisteredClientRepository,
        users: UserSecurityRepository,
        properties: McpOAuthProperties,
        jdbc: NamedParameterJdbcTemplate,
    ): OAuth2AuthorizationService = HashingMcpOAuthAuthorizationService(clients, users, properties, jdbc)

    private fun validateActor(
        jwt: Jwt,
        properties: McpOAuthProperties,
        users: UserSecurityRepository,
    ): OAuth2TokenValidatorResult {
        val row = jwt.subject?.let(users::findByUserId)
        val securityVersion =
            jwt.getClaimAsString("securityVersion")?.toLongOrNull()
                ?: (jwt.claims["securityVersion"] as? Number)?.toLong()
        return if (jwt.audience?.contains(properties.resourceUri) == true &&
            row != null &&
            row.status == "ACTIVE" &&
            row.securityVersion == securityVersion &&
            row.securityVersion > 0
        ) {
            OAuth2TokenValidatorResult.success()
        } else {
            OAuth2TokenValidatorResult.failure(OAuth2Error("invalid_token", "MCP actor or audience is invalid", null))
        }
    }

    private fun strictMapper(): JsonMapper = JsonMapper.builder().build()

    private fun metadataSha256(
        clientId: String,
        clientName: String,
        kind: String,
        redirectUris: List<String>,
        scopes: List<String>,
    ): String {
        val canonical = "$clientId\n$clientName\n$kind\n${redirectUris.joinToString("\n")}\n${scopes.sorted().joinToString("\n")}"
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(canonical.toByteArray()))
    }

    private fun readSecret(path: String): String {
        val bytes =
            Files.readAllBytes(
                java.nio.file.Path
                    .of(path),
            )
        return try {
            bytes.toString(Charsets.UTF_8)
        } finally {
            bytes.fill(0)
        }
    }
}
