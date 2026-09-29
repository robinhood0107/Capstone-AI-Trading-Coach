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
import com.capstone.decision.infrastructure.risk.ActorScopedReadQuery
import com.capstone.decision.infrastructure.risk.JdbcPrincipleSnapshotAdapter
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.context.ApplicationContext
import org.springframework.dao.DataAccessException
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.jdbc.datasource.DriverManagerDataSource
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import java.sql.DriverManager

@Testcontainers
@SpringBootTest(
    properties = [
        "spring.autoconfigure.exclude=org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration",
    ],
)
class JdbcPrincipleSnapshotAdapterIntegrationTest(
    @Autowired private val adapter: JdbcPrincipleSnapshotAdapter,
    @Autowired private val actorScopedReadQuery: ActorScopedReadQuery,
    @Autowired private val applicationContext: ApplicationContext,
    @Autowired private val actorCapabilityIssuer: TestActorCapabilityIssuer,
) : SpringApiIntegrationTestBase() {
    private val adminJdbc =
        JdbcTemplate(
            DriverManagerDataSource(postgres.jdbcUrl, postgres.username, postgres.password),
        )

    private val riskRunId = "auto_run_${"b".repeat(32)}"
    private val riskClaimHash = "sha256:${"c".repeat(64)}"
    private val riskAccountId = "acct_${"a".repeat(32)}"
    private val riskPolicyId = "auto_pol_${"d".repeat(32)}"
    private val riskBalanceId = "obs_risk_exclusion_${"e".repeat(24)}"

    @BeforeEach
    fun resetPrinciples() {
        cleanupAutomationRiskExclusionFixture()
        adminJdbc.update("delete from principle_versions")
        adminJdbc.update("delete from principles")
    }

    @AfterEach
    fun cleanupAutomationRiskExclusionFixture() {
        adminJdbc.update(
            "delete from portfolio_position_observations where balance_observation_id=?",
            riskBalanceId,
        )
        adminJdbc.update("delete from portfolio_balance_observations where observation_id=?", riskBalanceId)
        adminJdbc.update("delete from automation_positions where user_id='usr_demo_user' and account_id=?", riskAccountId)
        adminJdbc.update("delete from automation_runtime_claim where run_id=?", riskRunId)
        adminJdbc.update("delete from automation_runtime_checkpoint where run_id=?", riskRunId)
        adminJdbc.update("delete from automation_runs where run_id=?", riskRunId)
        adminJdbc.update("delete from automation_control where user_id='usr_demo_user'")
        adminJdbc.update("delete from automation_policy_versions where policy_id=?", riskPolicyId)
    }

    @Test
    fun `one owner scoped lookup pins the current immutable active version`() {
        insertPrincipleWithTwoVersions()

        val snapshot =
            asTestActor(actorCapabilityIssuer) {
                adapter.findActiveOwned(
                    actorUserId = "usr_demo_user",
                    principleId = PRINCIPLE_ID,
                )
            }
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
        TestPeerUser.ensure(adminJdbc)

        val crossOwner =
            asTestActor(actorCapabilityIssuer, TestPeerUser.USER_ID) {
                adapter.findActiveOwned(TestPeerUser.USER_ID, PRINCIPLE_ID)
            }
        adminJdbc.update(
            "update principles set status = 'ARCHIVED' where principle_id = ?",
            PRINCIPLE_ID.value,
        )
        val inactive = asTestActor(actorCapabilityIssuer) { adapter.findActiveOwned("usr_demo_user", PRINCIPLE_ID) }
        val missing =
            asTestActor(actorCapabilityIssuer) {
                adapter.findActiveOwned(
                    "usr_demo_user",
                    PrincipleId("prc_ffffffffffffffffffffffffffffffff"),
                )
            }

        assertThat(crossOwner).isNull()
        assertThat(inactive).isNull()
        assertThat(missing).isNull()
    }

    @Test
    fun `automation risk exclusions come from the owner balance and omit active bot positions`() {
        insertPrincipleWithTwoVersions()
        val owner = "usr_demo_user"
        val account = riskAccountId
        val runId = riskRunId
        val claimHash = riskClaimHash
        val sessionDate = java.time.LocalDate.now(java.time.ZoneId.of("Asia/Seoul"))
        val policyId = riskPolicyId
        val balanceId = riskBalanceId
        val accountScopeHash = account.removePrefix("acct_") + "0".repeat(32)

        adminJdbc.update(
            """
            insert into automation_policy_versions(
              policy_id,version,user_id,capital_limit_krw,stop_loss_bps,take_profit_bps,
              risk_profile,principle_id,principle_version_id,principle_version
            ) values (?,1,?,1000000,500,1000,'BALANCED',?,?,2)
            """.trimIndent(),
            policyId,
            owner,
            PRINCIPLE_ID.value,
            VERSION_TWO_ID,
        )
        adminJdbc.update(
            """
            insert into automation_control(
              user_id,control_state,version,brokerage_mode,account_id,principle_id,strategy_id,
              baseline_account_digest,certification_status,kill_switch_active
            ) values (?,'ARMED',1,'KIS_MOCK',?,?,'strategy_risk_exclusion_v1',repeat('f',64),'VALID',false)
            """.trimIndent(),
            owner,
            account,
            PRINCIPLE_ID.value,
        )
        adminJdbc.update(
            """
            insert into automation_runs(
              run_id,user_id,session_date,state,brokerage_mode,selected_symbol,selected_side,
              physical_submit_count,vertex_call_count,provider_calls,started_at,updated_at,
              principle_id,principle_version_id,principle_version
            ) values (?, ?, date '$sessionDate','SCHEDULED','KIS_MOCK',null,null,0,0,0,now(),now(),?,?,2)
            """.trimIndent(),
            runId,
            owner,
            PRINCIPLE_ID.value,
            VERSION_TWO_ID,
        )
        adminJdbc.update(
            """
            insert into automation_runtime_checkpoint(
              run_id,user_id,session_date,checkpoint_version,state,selected_symbol,selected_side,
              decision_id,vertex_call_count,provider_call_count,logical_submit_count,updated_at
            ) values (?, ?, ?,'1','SCHEDULED',null,null,null,0,0,0,now())
            """.trimIndent(),
            runId,
            owner,
            java.sql.Date.valueOf(sessionDate),
        )
        adminJdbc.update(
            """
            insert into automation_runtime_claim(
              user_id,session_date,run_id,claim_token_hash,claim_state,claimed_at
            ) values (?,date '$sessionDate',?,?,'ACTIVE',now())
            """.trimIndent(),
            owner,
            runId,
            claimHash,
        )
        adminJdbc.update(
            """
            insert into automation_positions(
              position_id,user_id,account_id,symbol,quantity,entry_session,expiry_session,status,
              bot_owned,short_allowed,created_at,entry_order_id,entry_ordered_quantity,
              entry_filled_quantity,entry_unfilled_quantity,entry_average_fill_price_krw,
              policy_id,policy_version,stop_loss_bps,take_profit_bps
            ) values (
              'auto_pos_${"1".repeat(32)}',?,?,'005930',1,date '2026-09-28',date '2026-10-01','OPEN',
              true,false,now(),'ord_mock_${"2".repeat(32)}',1,1,0,70000,?,1,500,1000
            )
            """.trimIndent(),
            owner,
            account,
            policyId,
        )
        adminJdbc.update(
            """
            insert into portfolio_balance_observations(
              observation_id,owner_user_id,account_scope_hash,source,context_status,cash_krw,
              portfolio_equity_krw,margin_requirement_krw,completeness,position_count,
              observed_at,received_at,schema_version,source_version,payload_json,source_ref,artifact_hash
            ) values (?,? ,?,'KIS_MOCK','ACTIVE',900000,1000000,0,'COMPLETE',2,now(),now(),
              '2','kis-mock-online-complete-v2','{"complete":true}'::jsonb,repeat('3',64),repeat('4',64))
            """.trimIndent(),
            balanceId,
            owner,
            accountScopeHash,
        )
        adminJdbc.update(
            """
            insert into portfolio_position_observations(
              balance_observation_id,symbol,quantity,market_value_krw,is_gold_etf_etn
            ) values (?, '005930',1,80000,false),(?, '086790',1,20000,false)
            """.trimIndent(),
            balanceId,
            balanceId,
        )

        val excluded =
            asTestActor(actorCapabilityIssuer, owner) {
                adapter.findAutomationRiskExcludedSymbols(
                    actorUserId = owner,
                    principleId = PRINCIPLE_ID,
                    runId = runId,
                    claimHash = claimHash,
                )
            }

        assertThat(excluded).containsExactly("086790")
        assertThrows<DataAccessException> {
            asTestActor(actorCapabilityIssuer, owner) {
                adapter.findAutomationRiskExcludedSymbols(
                    actorUserId = owner,
                    principleId = PRINCIPLE_ID,
                    runId = runId,
                    claimHash = "sha256:${"f".repeat(64)}",
                )
            }
        }

        adminJdbc.update("update automation_runtime_claim set claim_state='RELEASED',released_at=now() where run_id=?", runId)
        adminJdbc.update("update automation_runs set state='COMPLETED' where run_id=?", runId)
        adminJdbc.update("update automation_runtime_checkpoint set state='COMPLETED' where run_id=?", runId)
        val releasedFixture =
            adminJdbc.queryForMap(
                "select claim.claim_state,claim.session_date,run.state,run.principle_id,checkpoint.state as checkpoint_state " +
                    "from automation_runtime_claim claim join automation_runs run using(run_id) " +
                    "join automation_runtime_checkpoint checkpoint using(run_id) where claim.run_id=?",
                runId,
            )
        assertThat(releasedFixture["claim_state"]).isEqualTo("RELEASED")
        assertThat(releasedFixture["session_date"]).isEqualTo(java.sql.Date.valueOf(sessionDate))
        assertThat(releasedFixture["state"]).isEqualTo("COMPLETED")
        assertThat(releasedFixture["checkpoint_state"]).isEqualTo("COMPLETED")
        assertThat(releasedFixture["principle_id"]).isEqualTo(PRINCIPLE_ID.value)
        val sameDayContinuation =
            java.time.OffsetDateTime.of(sessionDate, java.time.LocalTime.of(14, 0), java.time.ZoneOffset.ofHours(9))
        val afterCutoffContinuation =
            java.time.OffsetDateTime.of(sessionDate, java.time.LocalTime.of(15, 21), java.time.ZoneOffset.ofHours(9))

        fun releasedContinuationAllowed(asOf: java.time.OffsetDateTime): Boolean =
            DriverManager
                .getConnection(postgres.jdbcUrl, "decision_automation_runtime", "automation-runtime-test-0001")
                .use { runtime ->
                    runtime
                        .prepareStatement("select p1_automation_released_continuation_claim_valid_v231(?,?,?,?,?)")
                        .use { query ->
                            query.setString(1, runId)
                            query.setString(2, claimHash)
                            query.setString(3, owner)
                            query.setString(4, PRINCIPLE_ID.value)
                            query.setObject(5, asOf)
                            query.executeQuery().use { rows ->
                                assertThat(rows.next()).isTrue()
                                rows.getBoolean(1)
                            }
                        }
                }
        assertThat(releasedContinuationAllowed(sameDayContinuation)).isTrue()
        assertThat(releasedContinuationAllowed(afterCutoffContinuation)).isFalse()

        adminJdbc.update("delete from portfolio_position_observations where balance_observation_id=?", balanceId)
        adminJdbc.update("delete from portfolio_balance_observations where observation_id=?", balanceId)
        adminJdbc.update("delete from automation_positions where user_id=? and account_id=?", owner, account)
        adminJdbc.update("delete from automation_runtime_claim where run_id=?", runId)
        adminJdbc.update("delete from automation_runtime_checkpoint where run_id=?", runId)
        adminJdbc.update("delete from automation_runs where run_id=?", runId)
        adminJdbc.update("delete from automation_control where user_id=?", owner)
        adminJdbc.update("delete from automation_policy_versions where policy_id=?", policyId)
    }

    @Test
    fun `automation risk observations require the exact actor source capability`() {
        val suffix =
            java.util.UUID
                .randomUUID()
                .toString()
                .replace("-", "")
        val ownerScope = "a".repeat(32) + suffix
        val peerScope = "b".repeat(32) + suffix
        TestPeerUser.ensure(adminJdbc)
        adminJdbc.update(
            """
            insert into deterministic_risk_observations(
              observation_id,owner_user_id,owner_scope_hash,portfolio_source,daily_loss_rate,
              max_drawdown,annualized_volatility,completeness,observed_at,received_at,
              schema_version,source_version,payload_json,source_ref,artifact_hash
            ) values
              (?, 'usr_demo_user', ?, 'KIS_MOCK', -0.02, -0.03, 0.10, 'COMPLETE', now(), now(),
                'deterministic-risk-observation.v1','p1-automation-risk-v1','{}'::jsonb,repeat('1',64),repeat('2',64)),
              (?, '${TestPeerUser.USER_ID}', ?, 'KIS_MOCK', -0.80, -0.90, 0.99, 'COMPLETE', now(), now(),
                'deterministic-risk-observation.v1','p1-automation-risk-v1','{}'::jsonb,repeat('3',64),repeat('4',64))
            """.trimIndent(),
            "risk_owner_$suffix",
            ownerScope,
            "risk_peer_$suffix",
            peerScope,
        )

        val ownerRisk =
            asTestActor(actorCapabilityIssuer, "usr_demo_user") {
                actorScopedReadQuery
                    .query(
                        actorUserId = "usr_demo_user",
                        sql = "SELECT daily_loss_rate FROM read_automation_risk_snapshot_authorized_v231(?, ?, ?)",
                        binder = { statement ->
                            statement.setString(1, "usr_demo_user")
                            statement.setString(2, ownerScope)
                            statement.setString(3, "KIS_MOCK")
                        },
                    ) { result -> result.getBigDecimal("daily_loss_rate") }
                    .single()
            }
        assertThat(ownerRisk).isEqualByComparingTo("-0.02")

        val crossOwnerFailure =
            assertThrows<java.sql.SQLException> {
                asTestActor(actorCapabilityIssuer, "usr_demo_user") {
                    actorScopedReadQuery.query(
                        actorUserId = "usr_demo_user",
                        sql = "SELECT daily_loss_rate FROM read_automation_risk_snapshot_authorized_v231(?, ?, ?)",
                        binder = { statement ->
                            statement.setString(1, TestPeerUser.USER_ID)
                            statement.setString(2, peerScope)
                            statement.setString(3, "KIS_MOCK")
                        },
                    ) { result -> result.getBigDecimal("daily_loss_rate") }
                }
            }
        assertThat(crossOwnerFailure.sqlState).isEqualTo("42501")

        val forgedScopeFailure =
            assertThrows<java.sql.SQLException> {
                DriverManager.getConnection(postgres.jdbcUrl, "decision_app", "app-test").use { app ->
                    app.autoCommit = false
                    app.createStatement().use {
                        it.execute("select set_config('app.actor_user_id','${TestPeerUser.USER_ID}',true)")
                    }
                    app.createStatement().use {
                        it
                            .executeQuery(
                                "select * from read_automation_risk_snapshot_authorized_v231(" +
                                    "'${TestPeerUser.USER_ID}','$peerScope','KIS_MOCK')",
                            ).use { rows -> rows.next() }
                    }
                }
            }
        assertThat(forgedScopeFailure.sqlState).isEqualTo("42501")
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
        adminJdbc.update(
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
                adminJdbc.queryForObject(
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
        adminJdbc.update(
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
            stablePostgresContainer(postgresImage)
                .withDatabaseName("decision_s2_2_snapshot")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")

        @DynamicPropertySource
        @JvmStatic
        fun postgresProperties(registry: DynamicPropertyRegistry) {
            registry.add("spring.datasource.url", postgres::getJdbcUrl)
            registry.add("spring.datasource.username") { "decision_app" }
            registry.add("spring.datasource.password") { "app-test" }
            registry.add("spring.flyway.user", postgres::getUsername)
            registry.add("spring.flyway.password", postgres::getPassword)
        }
    }
}
