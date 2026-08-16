package com.capstone.decision

import com.capstone.decision.application.signal.SignalReadSnapshot
import com.capstone.decision.application.signal.SignalStorageUnavailableException
import com.capstone.decision.application.signal.SignalV2Contract
import com.capstone.decision.application.signal.SignalV2ProductionReadPort
import com.capstone.decision.application.signal.StoredSignalComponent
import com.capstone.decision.infrastructure.security.DemoAccount
import com.capstone.decision.infrastructure.security.DemoAccounts
import com.capstone.decision.infrastructure.security.JwtService
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.boot.test.context.TestConfiguration
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Import
import org.springframework.context.annotation.Primary
import org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.get
import org.springframework.test.web.servlet.setup.DefaultMockMvcBuilder
import org.springframework.test.web.servlet.setup.MockMvcBuilders
import org.springframework.web.context.WebApplicationContext
import tools.jackson.databind.ObjectMapper
import java.time.Instant
import java.time.LocalDate

@SpringBootTest(
    properties = [
        "spring.autoconfigure.exclude=org.springframework.boot.jdbc.autoconfigure.DataSourceAutoConfiguration,org.springframework.boot.hibernate.autoconfigure.HibernateJpaAutoConfiguration,org.springframework.boot.data.jpa.autoconfigure.DataJpaRepositoriesAutoConfiguration,org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration",
    ],
)
@Import(TestAuthRepositoryConfiguration::class, SignalV2ApiTestConfiguration::class)
class SignalV2ApiIntegrationTest(
    @Autowired private val context: WebApplicationContext,
    @Autowired private val jwtService: JwtService,
    @Autowired private val fakePort: MutableSignalV2ReadPort,
    @Autowired private val objectMapper: ObjectMapper,
) : SpringApiIntegrationTestBase() {
    private lateinit var mockMvc: MockMvc

    @BeforeEach
    fun setUp() {
        fakePort.fail = false
        fakePort.snapshot = SignalReadSnapshot(emptyList(), null)
        mockMvc =
            MockMvcBuilders
                .webAppContextSetup(context)
                .apply<DefaultMockMvcBuilder>(springSecurity())
                .build()
    }

    @Test
    fun `Signal v2 requires authentication`() {
        mockMvc.get("/api/v2/signals/005930").andExpect {
            status { isUnauthorized() }
            jsonPath("$.error.code") { value("UNAUTHORIZED") }
        }
    }

    @Test
    fun `no evidence returns 200 all-abstain no-store without root time`() {
        val response =
            mockMvc
                .get("/api/v2/signals/005930") {
                    header("Authorization", "Bearer ${token()}")
                    header("X-Request-Id", "req-signal-all-abstain")
                }.andExpect {
                    status { isOk() }
                    header { string("Cache-Control", "no-store") }
                    jsonPath("$.success") { value(true) }
                    jsonPath("$.data.symbol") { value("005930") }
                    jsonPath("$.data.asOf") { doesNotExist() }
                    jsonPath("$.data.modelReportId") { doesNotExist() }
                    jsonPath("$.data.composite.status") { value("ABSTAIN") }
                    jsonPath("$.data.components.ruleBaseline.reason") { value("MISSING_EVIDENCE") }
                    jsonPath("$.data.components.hmmRegime.state") { doesNotExist() }
                    jsonPath("$.data.components.hmmRegime.asOf") { doesNotExist() }
                }.andReturn()
                .response

        // 실제 HTTP serializer의 data field set을 closed runtime parser로 다시 검증한다.
        val data = objectMapper.readTree(response.contentAsByteArray).path("data")
        SignalV2Contract.validateRuntime(objectMapper.writeValueAsBytes(data))
    }

    @Test
    fun `query and invalid symbol are rejected and storage failure is 503`() {
        val bearer = "Bearer ${token()}"
        mockMvc
            .get("/api/v2/signals/005930?artifactId=x") { header("Authorization", bearer) }
            .andExpect { status { isBadRequest() } }
        mockMvc
            .get("/api/v2/signals/005930;DROP") { header("Authorization", bearer) }
            .andExpect { status { isBadRequest() } }

        fakePort.fail = true
        mockMvc
            .get("/api/v2/signals/005930") { header("Authorization", bearer) }
            .andExpect {
                status { isServiceUnavailable() }
                jsonPath("$.error.code") { value("SIGNAL_UNAVAILABLE") }
            }
    }

    @Test
    fun `partial HOLD and stale evidence serialize the closed runtime union`() {
        val completed = LocalDate.of(2026, 8, 14)
        fakePort.snapshot =
            SignalReadSnapshot(
                listOf(
                    StoredSignalComponent(
                        producer = "LIGHTGBM",
                        sourceWorkspace = "decision-platform",
                        sessionDate = completed,
                        asOf = Instant.parse("2026-08-14T06:30:00Z"),
                        status = "AVAILABLE",
                        reason = null,
                        signal = "HOLD",
                        confidence = 0.0,
                        predictedReturn = null,
                        modelVersion = "lgbm-v1-fixture",
                        modelReportId = "mrp-fixture",
                    ),
                    StoredSignalComponent(
                        producer = "LSTM",
                        sourceWorkspace = "return-engine",
                        sessionDate = completed.minusDays(1),
                        asOf = Instant.parse("2026-08-13T06:30:00Z"),
                        status = "AVAILABLE",
                        reason = null,
                        signal = "BUY",
                        confidence = 0.8,
                        predictedReturn = 0.01,
                        modelVersion = "lstm-v1-fixture",
                        modelReportId = "mrp-lstm-fixture",
                    ),
                ),
                completed,
            )

        val response =
            mockMvc
                .get("/api/v2/signals/005930") { header("Authorization", "Bearer ${token()}") }
                .andExpect {
                    status { isOk() }
                    jsonPath("$.data.asOf") { value("2026-08-14T06:30:00Z") }
                    jsonPath("$.data.components.lightgbm.status") { value("AVAILABLE") }
                    jsonPath("$.data.components.lightgbm.signal") { value("HOLD") }
                    jsonPath("$.data.components.lstm.status") { value("ABSTAIN") }
                    jsonPath("$.data.components.lstm.reason") { value("STALE_EVIDENCE") }
                    jsonPath("$.data.components.lstm.asOf") { doesNotExist() }
                    jsonPath("$.data.composite.reason") { value("REQUIRED_COMPONENT_UNAVAILABLE") }
                }.andReturn()
                .response

        SignalV2Contract.validateRuntime(
            objectMapper.writeValueAsBytes(objectMapper.readTree(response.contentAsByteArray).path("data")),
        )
    }

    @Test
    fun `drift suspension remains ARTIFACT_DRIFT even when the prior batch session is old`() {
        fakePort.snapshot =
            SignalReadSnapshot(
                listOf(
                    StoredSignalComponent(
                        producer = "LIGHTGBM",
                        sourceWorkspace = "decision-platform",
                        sessionDate = LocalDate.of(2026, 8, 13),
                        asOf = null,
                        status = "ABSTAIN",
                        reason = "ARTIFACT_DRIFT",
                        signal = null,
                        confidence = null,
                        predictedReturn = null,
                        modelVersion = null,
                        modelReportId = null,
                    ),
                ),
                LocalDate.of(2026, 8, 14),
            )

        mockMvc
            .get("/api/v2/signals/005930") { header("Authorization", "Bearer ${token()}") }
            .andExpect {
                status { isOk() }
                jsonPath("$.data.components.lightgbm.status") { value("ABSTAIN") }
                jsonPath("$.data.components.lightgbm.reason") { value("ARTIFACT_DRIFT") }
                jsonPath("$.data.components.lightgbm.asOf") { doesNotExist() }
            }
    }

    private fun token(): String {
        val identity = requireNotNull(DemoAccounts.byUsername("demo-user"))
        return jwtService
            .issue(
                DemoAccount(
                    userId = identity.userId,
                    username = identity.username,
                    role = identity.role,
                    securityVersion = 1,
                ),
            ).token
    }
}

class MutableSignalV2ReadPort : SignalV2ProductionReadPort {
    @Volatile
    var fail: Boolean = false

    @Volatile
    var snapshot: SignalReadSnapshot = SignalReadSnapshot(emptyList(), null)

    override fun find(symbol: String): SignalReadSnapshot {
        if (fail) {
            throw SignalStorageUnavailableException()
        }
        return snapshot
    }
}

@TestConfiguration
class SignalV2ApiTestConfiguration {
    @Bean
    fun mutableSignalV2ReadPort(): MutableSignalV2ReadPort = MutableSignalV2ReadPort()

    @Bean
    @Primary
    fun signalV2ProductionReadPort(port: MutableSignalV2ReadPort): SignalV2ProductionReadPort = port
}
