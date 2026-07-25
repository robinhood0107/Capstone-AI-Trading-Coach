package com.capstone.decision

import com.capstone.decision.application.risk.MetricSnapshotAssembler
import com.capstone.decision.application.risk.PortfolioEvaluationUseCase
import com.capstone.decision.application.risk.port.BalancePort
import com.capstone.decision.application.risk.port.DisclosureRiskPort
import com.capstone.decision.application.risk.port.InstrumentCatalogPort
import com.capstone.decision.application.risk.port.MarginPort
import com.capstone.decision.application.risk.port.NewsEvidencePort
import com.capstone.decision.application.risk.port.OrderMetricPort
import com.capstone.decision.application.risk.port.PortfolioContextPort
import com.capstone.decision.application.risk.port.PricePort
import com.capstone.decision.application.risk.port.RiskSnapshotPort
import com.capstone.decision.application.risk.port.SignalPort
import com.capstone.decision.domain.principle.PrincipleId
import com.capstone.decision.infrastructure.risk.JdbcPrincipleSnapshotAdapter
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.context.ApplicationContext
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName

@Testcontainers
@SpringBootTest(
    properties = [
        "spring.autoconfigure.exclude=org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration",
    ],
)
class JdbcPrincipleSnapshotAdapterIntegrationTest(
    @Autowired private val jdbcTemplate: JdbcTemplate,
    @Autowired private val adapter: JdbcPrincipleSnapshotAdapter,
    @Autowired private val applicationContext: ApplicationContext,
) : SpringApiIntegrationTestBase() {
    @BeforeEach
    fun resetPrinciples() {
        jdbcTemplate.update("delete from principle_versions")
        jdbcTemplate.update("delete from principles")
    }

    @Test
    fun `one owner scoped lookup pins the current immutable active version`() {
        insertPrincipleWithTwoVersions()

        val snapshot =
            adapter.findActiveOwned(
                actorUserId = "usr_demo_user",
                principleId = PRINCIPLE_ID,
            )
        val pinned = requireNotNull(snapshot)

        assertThat(snapshot).isNotNull
        assertThat(pinned.principleVersionId.value).isEqualTo(VERSION_TWO_ID)
        assertThat(pinned.version).isEqualTo(2)
        assertThat(pinned.rules).hasSize(8)
        assertThat(pinned.rules).allMatch { it.evidenceRequirement.name in setOf("REQUIRED", "OPTIONAL") }
    }

    @Test
    fun `missing cross owner and inactive targets are the same not found result`() {
        insertPrincipleWithTwoVersions()

        val crossOwner = adapter.findActiveOwned("usr_demo_admin", PRINCIPLE_ID)
        jdbcTemplate.update(
            "update principles set status = 'ARCHIVED' where principle_id = ?",
            PRINCIPLE_ID.value,
        )
        val inactive = adapter.findActiveOwned("usr_demo_user", PRINCIPLE_ID)
        val missing =
            adapter.findActiveOwned(
                "usr_demo_user",
                PrincipleId("prc_ffffffffffffffffffffffffffffffff"),
            )

        assertThat(crossOwner).isNull()
        assertThat(inactive).isNull()
        assertThat(missing).isNull()
    }

    @Test
    fun `S2_3 production context exposes only stored observation or typed unavailable source adapters`() {
        val expectedBeans =
            mapOf(
                PricePort::class.java to setOf("jdbcMarketQuoteAdapter"),
                BalancePort::class.java to
                    setOf(
                        "jdbcKisMockBalanceAdapter",
                        "jdbcInternalPaperBalanceAdapter",
                    ),
                MarginPort::class.java to setOf("jdbcStoredMarginAdapter"),
                OrderMetricPort::class.java to setOf("jdbcDailyOrderCountAdapter"),
                RiskSnapshotPort::class.java to setOf("jdbcDeterministicRiskAdapter"),
                InstrumentCatalogPort::class.java to setOf("jdbcInstrumentCatalogAdapter"),
                NewsEvidencePort::class.java to setOf("decisionNewsEvidencePort"),
                DisclosureRiskPort::class.java to setOf("grpcDisclosureRiskAdapter"),
                SignalPort::class.java to setOf("decisionSignalPort"),
                PortfolioContextPort::class.java to setOf("jdbcPortfolioContextAdapter"),
            )

        expectedBeans.forEach { (portType, beanNames) ->
            val actualBeans = applicationContext.getBeansOfType(portType)
            assertThat(actualBeans.keys).containsExactlyInAnyOrderElementsOf(beanNames)
            assertThat(actualBeans.keys).noneMatch { it.contains("fake", ignoreCase = true) }
        }
        assertThat(applicationContext.getBeansOfType(MetricSnapshotAssembler::class.java))
            .containsOnlyKeys("decisionMetricSnapshotAssembler")
        assertThat(applicationContext.getBeansOfType(PortfolioEvaluationUseCase::class.java))
            .containsOnlyKeys("decisionPortfolioEvaluationUseCase")
        assertThat(applicationContext.getBeansOfType(JdbcPrincipleSnapshotAdapter::class.java))
            .containsOnlyKeys("jdbcPrincipleSnapshotAdapter")
    }

    private fun insertPrincipleWithTwoVersions() {
        jdbcTemplate.update(
            """
            insert into principles (
              principle_id, user_id, preset_id, title, mode, status, current_version
            )
            values (?, 'usr_demo_user', 'balanced', 'S2.2 pinned snapshot', 'GUIDE', 'ACTIVE', 2)
            """.trimIndent(),
            PRINCIPLE_ID.value,
        )
        val rulesJson =
            requireNotNull(
                jdbcTemplate.queryForObject(
                    "select rules_json::text from principle_presets where preset_id = 'balanced'",
                    String::class.java,
                ),
            )
        insertVersion(VERSION_ONE_ID, 1, rulesJson)
        insertVersion(VERSION_TWO_ID, 2, rulesJson)
    }

    private fun insertVersion(
        versionId: String,
        version: Int,
        rulesJson: String,
    ) {
        jdbcTemplate.update(
            """
            insert into principle_versions (
              principle_version_id, principle_id, version, preset_id, title, mode, status,
              rules_json, changed_fields, created_by
            )
            values (
              ?, ?, ?, 'balanced', 'S2.2 pinned snapshot', 'GUIDE', 'ACTIVE',
              ?::jsonb, ARRAY['rules']::text[], 'usr_demo_user'
            )
            """.trimIndent(),
            versionId,
            PRINCIPLE_ID.value,
            version,
            rulesJson,
        )
    }

    companion object {
        private val PRINCIPLE_ID = PrincipleId("prc_0123456789abcdef0123456789abcdef")
        private const val VERSION_ONE_ID = "pvr_11111111111111111111111111111111"
        private const val VERSION_TWO_ID = "pvr_22222222222222222222222222222222"
        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            PostgreSQLContainer(postgresImage)
                .withDatabaseName("decision_s2_2_snapshot")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")

        @DynamicPropertySource
        @JvmStatic
        fun postgresProperties(registry: DynamicPropertyRegistry) {
            registry.add("spring.datasource.url", postgres::getJdbcUrl)
            registry.add("spring.datasource.username", postgres::getUsername)
            registry.add("spring.datasource.password", postgres::getPassword)
            registry.add("spring.flyway.user", postgres::getUsername)
            registry.add("spring.flyway.password", postgres::getPassword)
        }
    }
}
