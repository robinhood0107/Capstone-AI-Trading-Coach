package com.capstone.decision

import com.capstone.decision.application.automation.AutomationService
import com.capstone.decision.application.automation.DisarmAutomationCommand
import com.capstone.decision.application.brokerage.MockConnectionPosition
import com.capstone.decision.application.brokerage.MockConnectionProof
import com.capstone.decision.application.security.ActorRlsScopePort
import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.infrastructure.brokerage.BrokerageCredentialCrypto
import com.capstone.decision.infrastructure.brokerage.BrokerageKekFile
import com.capstone.decision.infrastructure.brokerage.MockCredentialCertificationRepository
import com.capstone.decision.infrastructure.brokerage.MockCredentialConnectionRepository
import com.capstone.decision.infrastructure.brokerage.MockCredentialDisconnectRepository
import com.capstone.decision.infrastructure.brokerage.MockCredentialSettingsService
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNotEquals
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.context.ApplicationContext
import org.springframework.dao.PessimisticLockingFailureException
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken
import org.springframework.security.core.context.SecurityContextHolder
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.springframework.transaction.PlatformTransactionManager
import org.springframework.transaction.support.TransactionTemplate
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermission
import java.security.MessageDigest
import java.security.SecureRandom
import java.sql.DriverManager
import java.sql.SQLException
import java.sql.Timestamp
import java.time.LocalDate
import java.time.ZoneId

@Testcontainers
@SpringBootTest(
    properties = ["spring.autoconfigure.exclude=org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration"],
)
class BoundMockCredentialIntegrationTest(
    @Autowired private val context: ApplicationContext,
    @Autowired private val actorRlsScope: ActorRlsScopePort,
    @Autowired private val transactionManager: PlatformTransactionManager,
    @Autowired private val testActorCapabilityIssuer: TestActorCapabilityIssuer,
) : SpringApiIntegrationTestBase() {
    @TempDir
    lateinit var root: Path

    @BeforeEach
    fun ensurePeerOwner() {
        TestPeerUser.ensure(postgres.jdbcUrl, postgres.username, postgres.password)
    }

    @AfterEach
    fun leaveDemoAutomationStopped() {
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.executeUpdate(
                    "update automation_runtime_claim set claim_state='RELEASED'," +
                        "released_at=COALESCE(released_at,statement_timestamp()) " +
                        "where user_id='usr_demo_user' and claim_state='ACTIVE'",
                )
                statement.executeUpdate(
                    "update automation_runtime_schedule set schedule_state='HALTED'," +
                        "updated_at=statement_timestamp() where user_id='usr_demo_user' " +
                        "and schedule_state in ('ARMED','CLAIMED')",
                )
                statement.executeUpdate(
                    "update automation_control set control_state='DISARMED',version=version+1," +
                        "updated_at=statement_timestamp() where user_id='usr_demo_user' " +
                        "and control_state<>'DISARMED'",
                )
            }
        }
    }

    @Test
    fun `owner removes only their settled mock credential`() {
        val directory = prepareKeyDirectory()
        val crypto = BrokerageCredentialCrypto(BrokerageKekFile(directory.toString()))
        val appKey = "K" + "A".repeat(19)
        val appSecret = "S" + "B".repeat(39)
        val accountNo = "5" + "0".repeat(9)
        val service =
            MockCredentialSettingsService(
                context.getBeanProvider(NamedParameterJdbcTemplate::class.java),
                actorRlsScope,
                crypto,
            )
        val repository =
            MockCredentialDisconnectRepository(
                context.getBeanProvider(NamedParameterJdbcTemplate::class.java),
                actorRlsScope,
                crypto,
            )
        val transaction = TransactionTemplate(transactionManager)
        asActor("usr_demo_user") {
            transaction.executeWithoutResult {
                service.save("usr_demo_user", appKey, appSecret, accountNo)
            }
        }
        asActor(TestPeerUser.USER_ID) {
            transaction.executeWithoutResult {
                service.save(TestPeerUser.USER_ID, "Z" + "C".repeat(19), "T" + "D".repeat(39), "6" + "1".repeat(9))
            }
        }
        val own = requireNotNull(asActor("usr_demo_user") { transaction.execute { service.summary("usr_demo_user") } })
        asActor(TestPeerUser.USER_ID) {
            assertThrows(IllegalStateException::class.java) {
                transaction.execute { repository.disconnect("usr_demo_user", own.accountId, own.revision) }
            }
        }
        val result =
            asActor("usr_demo_user") {
                transaction.execute { repository.disconnect("usr_demo_user", own.accountId, own.revision) }
            }
        assertEquals("REMOVED", result)
        assertEquals(null, asActor("usr_demo_user") { transaction.execute { service.summary("usr_demo_user") } })
        asActor("usr_demo_user") {
            transaction.executeWithoutResult { service.save("usr_demo_user", appKey, appSecret, accountNo) }
        }
        val readded =
            requireNotNull(asActor("usr_demo_user") { transaction.execute { service.summary("usr_demo_user") } })
        assertEquals(own.accountId, readded.accountId, "the same full account number must retain its internal ID after deletion")
        assertNotNull(asActor(TestPeerUser.USER_ID) { transaction.execute { service.summary(TestPeerUser.USER_ID) } })
    }

    @Test
    fun `owner stores encrypted mock values and rotation resets connection without exposing another owner`() {
        val directory = prepareKeyDirectory()
        val service =
            MockCredentialSettingsService(
                context.getBeanProvider(NamedParameterJdbcTemplate::class.java),
                actorRlsScope,
                BrokerageCredentialCrypto(BrokerageKekFile(directory.toString())),
            )
        val key = "K" + "A".repeat(19)
        val secret = "S" + "B".repeat(39)
        val accountNo = "5" + "0".repeat(9)
        val transaction = TransactionTemplate(transactionManager)
        asActor("usr_demo_user") {
            transaction.executeWithoutResult { service.save("usr_demo_user", key, secret, accountNo) }
        }
        val first = asActor("usr_demo_user") { transaction.execute { service.summary("usr_demo_user") } }
        val firstSummary = requireNotNull(first)
        val firstRevision = firstSummary.revision
        assertEquals("STORED", firstSummary.state)
        assertTrue(firstRevision >= 1)
        assertFalse(first?.connected ?: true)
        assertFalse(first?.certified ?: true)
        assertEquals("AAAA", first?.appKeyLast4)
        assertEquals("0000", first?.accountNoLast4)
        assertTrue(first?.accountId?.matches(Regex("^acct_[0-9a-f]{32}$")) == true)
        val firstAccountId = requireNotNull(first?.accountId)
        val connectionRepository =
            MockCredentialConnectionRepository(
                context.getBeanProvider(NamedParameterJdbcTemplate::class.java),
                actorRlsScope,
            )
        asActor("usr_demo_user") {
            transaction.execute {
                service.resolveEnvelope("usr_demo_user", firstAccountId).use { envelope ->
                    assertEquals("STORED", envelope.state)
                    assertEquals(firstRevision, envelope.revision)
                    BrokerageCredentialCrypto(BrokerageKekFile(directory.toString()))
                        .open("usr_demo_user", firstAccountId, envelope.sealed)
                        .use { opened ->
                            assertEquals(key, opened.appKey.toString(Charsets.US_ASCII))
                            assertEquals(secret, opened.appSecret.toString(Charsets.US_ASCII))
                            assertEquals(accountNo, opened.accountNo.toString(Charsets.US_ASCII))
                        }
                }
            }
        }
        asActor("usr_demo_user") {
            transaction.executeWithoutResult {
                connectionRepository.beginAttempt("usr_demo_user", firstAccountId, firstRevision)
            }
            assertThrows(PessimisticLockingFailureException::class.java) {
                transaction.executeWithoutResult {
                    connectionRepository.beginAttempt("usr_demo_user", firstAccountId, firstRevision)
                }
            }
            transaction.executeWithoutResult {
                connectionRepository.markConnected("usr_demo_user", firstAccountId, firstRevision)
            }
        }
        val connected = asActor("usr_demo_user") { transaction.execute { service.summary("usr_demo_user") } }
        assertEquals("CONNECTED", connected?.state)
        assertTrue(connected?.connected == true)
        assertFalse(connected?.certified ?: true)

        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection.prepareStatement("select secret_ciphertext from user_broker_credentials where owner_user_id = ?").use { statement ->
                statement.setString(1, "usr_demo_user")
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    assertFalse(result.getBytes(1).contentEquals(key.toByteArray()))
                    assertFalse(result.next())
                }
            }
            val auditSql =
                "select target_id, payload_json::text from audit_logs where user_id = ? and action = 'MOCK_CREDENTIAL_STORED' and target_id = ? order by created_at desc limit 1"
            connection.prepareStatement(auditSql).use { statement ->
                statement.setString(1, "usr_demo_user")
                statement.setString(2, firstAccountId)
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    assertEquals(firstAccountId, result.getString("target_id"))
                    val payload = result.getString(2)
                    assertFalse(payload.contains(key))
                    assertFalse(payload.contains(secret))
                    assertFalse(payload.contains(accountNo))
                    assertFalse(result.next())
                }
            }
        }
        asActor(TestPeerUser.USER_ID) {
            assertThrows(IllegalStateException::class.java) {
                transaction.execute { service.summary("usr_demo_user") }
            }
            assertThrows(IllegalStateException::class.java) {
                transaction.executeWithoutResult {
                    connectionRepository.markConnected("usr_demo_user", firstAccountId, 1)
                }
            }
        }
        asActor(TestPeerUser.USER_ID) {
            transaction.executeWithoutResult {
                service.save(TestPeerUser.USER_ID, "Z" + "C".repeat(19), "T" + "D".repeat(39), "6" + "1".repeat(9))
            }
        }
        val otherAccountId =
            asActor(TestPeerUser.USER_ID) {
                transaction.execute { service.summary(TestPeerUser.USER_ID)?.accountId }
            }
        assertNotNull(otherAccountId)
        asActor("usr_demo_user") {
            assertThrows(IllegalStateException::class.java) {
                transaction.execute { service.resolveEnvelope("usr_demo_user", requireNotNull(otherAccountId)).use { } }
            }
        }
        asActor("usr_demo_user") {
            transaction.executeWithoutResult { service.save("usr_demo_user", key, secret, accountNo) }
        }
        val rotated = asActor("usr_demo_user") { transaction.execute { service.summary("usr_demo_user") } }
        assertEquals(firstRevision + 1, rotated?.revision)
        // 같은 전체 계좌번호를 다시 저장하면 내부 식별자를 그대로 쓴다. 새 식별자를 쓰면 그 계좌의
        // 주문·보유·잔고 관측 이력이 옛 식별자에 남아 화면과 무장에서 사라진다.
        assertEquals(firstAccountId, rotated?.accountId)
        assertEquals("STORED", rotated?.state)
        assertFalse(rotated?.connected ?: true)
        asActor("usr_demo_user") {
            // 연결 상태는 초기화된다: 옛 revision 으로는 연결됨을 찍지 못한다.
            assertThrows(PessimisticLockingFailureException::class.java) {
                transaction.executeWithoutResult {
                    connectionRepository.markConnected("usr_demo_user", firstAccountId, firstRevision)
                }
            }
        }
        // 다른 전체 계좌번호는 새 식별자를 쓴다. 두 계좌의 이력을 한 식별자에 섞지 않는다.
        asActor("usr_demo_user") {
            transaction.executeWithoutResult { service.save("usr_demo_user", key, secret, "7" + "2".repeat(9)) }
        }
        val moved = asActor("usr_demo_user") { transaction.execute { service.summary("usr_demo_user") } }
        assertEquals("2222", moved?.accountNoLast4)
        assertNotEquals(firstAccountId, moved?.accountId)
        asActor("usr_demo_user") {
            assertThrows(IllegalStateException::class.java) {
                transaction.execute { service.resolveEnvelope("usr_demo_user", firstAccountId).use { } }
            }
        }
    }

    @Test
    fun `mock order certification is owner and credential revision bound`() {
        val directory = prepareKeyDirectory()
        val crypto = BrokerageCredentialCrypto(BrokerageKekFile(directory.toString()))
        val settings =
            MockCredentialSettingsService(
                context.getBeanProvider(NamedParameterJdbcTemplate::class.java),
                actorRlsScope,
                crypto,
            )
        val connection =
            MockCredentialConnectionRepository(
                context.getBeanProvider(NamedParameterJdbcTemplate::class.java),
                actorRlsScope,
            )
        val certifications =
            MockCredentialCertificationRepository(
                context.getBeanProvider(NamedParameterJdbcTemplate::class.java),
                actorRlsScope,
            )
        val transaction = TransactionTemplate(transactionManager)
        asActor("usr_demo_user") {
            transaction.executeWithoutResult {
                settings.save("usr_demo_user", "K" + "A".repeat(19), "S" + "B".repeat(39), "5" + "0".repeat(9))
            }
        }
        asActor(TestPeerUser.USER_ID) {
            transaction.executeWithoutResult {
                settings.save(TestPeerUser.USER_ID, "Z" + "C".repeat(19), "T" + "D".repeat(39), "6" + "1".repeat(9))
            }
        }
        val userCredential = requireNotNull(asActor("usr_demo_user") { transaction.execute { settings.summary("usr_demo_user") } })
        val adminCredential =
            requireNotNull(asActor(TestPeerUser.USER_ID) { transaction.execute { settings.summary(TestPeerUser.USER_ID) } })
        asActor("usr_demo_user") {
            transaction.executeWithoutResult {
                connection.beginAttempt("usr_demo_user", userCredential.accountId, userCredential.revision)
                connection.markConnected("usr_demo_user", userCredential.accountId, userCredential.revision)
            }
        }
        asActor(TestPeerUser.USER_ID) {
            transaction.executeWithoutResult {
                connection.beginAttempt(TestPeerUser.USER_ID, adminCredential.accountId, adminCredential.revision)
                connection.markConnected(TestPeerUser.USER_ID, adminCredential.accountId, adminCredential.revision)
            }
        }
        val lease =
            "sha256:" +
                MessageDigest
                    .getInstance("SHA-256")
                    .digest("lease-alice".toByteArray())
                    .joinToString("") { "%02x".format(it) }
        val attempt =
            requireNotNull(
                asActor("usr_demo_user") {
                    transaction.execute {
                        certifications.begin("usr_demo_user", userCredential.accountId, userCredential.revision, lease)
                    }
                },
            )
        assertFalse(attempt.alreadyCertified)

        asActor("usr_demo_user") {
            assertThrows(Exception::class.java) {
                transaction.executeWithoutResult {
                    settings.save("usr_demo_user", "M" + "D".repeat(19), "N" + "E".repeat(39), "5" + "0".repeat(9))
                }
            }
            transaction.executeWithoutResult {
                certifications.complete(
                    ownerUserId = "usr_demo_user",
                    accountId = userCredential.accountId,
                    revision = userCredential.revision,
                    attempt = attempt,
                    leaseTokenSha256 = lease,
                    receiptSha256 = "a".repeat(64),
                    sessionDate = attempt.sessionDate,
                    quoteCalls = 1,
                    brokerageCalls = 7,
                    tokenCalls = 0,
                )
            }
        }
        val certified = requireNotNull(asActor("usr_demo_user") { transaction.execute { settings.summary("usr_demo_user") } })
        val other = requireNotNull(asActor(TestPeerUser.USER_ID) { transaction.execute { settings.summary(TestPeerUser.USER_ID) } })
        assertEquals("CERTIFIED", certified.state)
        assertEquals("PASS", certified.certificationStatus)
        assertEquals("CONNECTED", other.state)
        assertEquals("NOT_STARTED", other.certificationStatus)

        val recoveryLease =
            "sha256:" +
                MessageDigest
                    .getInstance("SHA-256")
                    .digest("lease-bob".toByteArray())
                    .joinToString("") { "%02x".format(it) }
        val recoveryAttempt =
            requireNotNull(
                asActor(TestPeerUser.USER_ID) {
                    transaction.execute {
                        certifications.begin(
                            TestPeerUser.USER_ID,
                            adminCredential.accountId,
                            adminCredential.revision,
                            recoveryLease,
                        )
                    }
                },
            )
        asActor(TestPeerUser.USER_ID) {
            transaction.executeWithoutResult {
                certifications.finish(
                    ownerUserId = TestPeerUser.USER_ID,
                    accountId = adminCredential.accountId,
                    revision = adminCredential.revision,
                    attempt = recoveryAttempt,
                    leaseTokenSha256 = recoveryLease,
                    status = "RECOVERY_REQUIRED",
                    failureCode = "TEST_ORDER_UNCERTAIN",
                    sessionDate = recoveryAttempt.sessionDate,
                    quoteCalls = 1,
                    brokerageCalls = 4,
                    tokenCalls = 0,
                )
            }
        }
        val recoverySummary =
            requireNotNull(asActor(TestPeerUser.USER_ID) { transaction.execute { settings.summary(TestPeerUser.USER_ID) } })
        assertEquals("CONNECTED", recoverySummary.state)
        assertEquals("RECOVERY_REQUIRED", recoverySummary.certificationStatus)
        asActor(TestPeerUser.USER_ID) {
            assertThrows(Exception::class.java) {
                transaction.executeWithoutResult {
                    settings.save(TestPeerUser.USER_ID, "Q" + "R".repeat(19), "S" + "T".repeat(39), "6" + "1".repeat(9))
                }
            }
            transaction.executeWithoutResult {
                certifications.acknowledgeRecovery(
                    TestPeerUser.USER_ID,
                    adminCredential.accountId,
                    adminCredential.revision,
                )
            }
        }
        val acknowledged = requireNotNull(asActor(TestPeerUser.USER_ID) { transaction.execute { settings.summary(TestPeerUser.USER_ID) } })
        assertEquals("CONNECTED", acknowledged.state)
        assertEquals("FAILED", acknowledged.certificationStatus)

        asActor("usr_demo_user") {
            transaction.executeWithoutResult {
                settings.save("usr_demo_user", "M" + "D".repeat(19), "N" + "E".repeat(39), "5" + "0".repeat(9))
            }
        }
        val rotated = requireNotNull(asActor("usr_demo_user") { transaction.execute { settings.summary("usr_demo_user") } })
        assertEquals("STORED", rotated.state)
        assertEquals("NOT_STARTED", rotated.certificationStatus)
        assertEquals(userCredential.revision + 1, rotated.revision)
        asActor(TestPeerUser.USER_ID) {
            transaction.executeWithoutResult {
                settings.save(TestPeerUser.USER_ID, "Q" + "R".repeat(19), "S" + "T".repeat(39), "6" + "1".repeat(9))
            }
        }
    }

    @Test
    fun `decision_app cannot read a credential with a forged owner GUC`() {
        DriverManager.getConnection(postgres.jdbcUrl, "decision_app", "app-test").use { connection ->
            connection.autoCommit = false
            connection.createStatement().use { statement ->
                statement.execute("select set_config('app.actor_user_id','usr_demo_user',true)")
            }
            val denied =
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.executeQuery("select * from read_bound_mock_broker_summary_v3('usr_demo_user')")
                    }
                }
            assertEquals("42501", denied.sqlState)
            connection.rollback()
            connection.createStatement().use { statement ->
                statement.execute("select set_config('app.actor_user_id','usr_demo_user',true)")
            }
            val envelopeDenied =
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.executeQuery(
                            "select * from read_bound_mock_broker_envelope_v3('usr_demo_user','acct_" + "0".repeat(32) + "')",
                        )
                    }
                }
            assertEquals("42501", envelopeDenied.sqlState)
            connection.rollback()
            connection.createStatement().use { statement ->
                statement.execute("select set_config('app.actor_user_id','usr_demo_user',true)")
            }
            val disconnectDenied =
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.executeQuery(
                            "select disconnect_bound_mock_broker_credential_v1('usr_demo_user','acct_" + "0".repeat(32) + "',1)",
                        )
                    }
                }
            assertEquals("42501", disconnectDenied.sqlState)
            connection.rollback()
            connection.createStatement().use { statement ->
                statement.execute("select set_config('app.actor_user_id','usr_demo_user',true)")
            }
            val certificationAttemptDenied =
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.executeQuery("select count(*) from user_broker_credential_certification_attempts")
                    }
                }
            assertEquals("42501", certificationAttemptDenied.sqlState)
            connection.rollback()
            val directCertificationDenied =
                assertThrows(SQLException::class.java) {
                    connection
                        .prepareStatement(
                            "update user_broker_credentials set credential_state='CERTIFIED' where owner_user_id=?",
                        ).use { statement ->
                            statement.setString(1, "usr_demo_user")
                            statement.executeUpdate()
                        }
                }
            assertEquals("42501", directCertificationDenied.sqlState)
        }
    }

    @Test
    fun `connection check balance becomes the owner's online observation and only for the bound account`() {
        val directory = prepareKeyDirectory()
        val service =
            MockCredentialSettingsService(
                context.getBeanProvider(NamedParameterJdbcTemplate::class.java),
                actorRlsScope,
                BrokerageCredentialCrypto(BrokerageKekFile(directory.toString())),
            )
        val connection =
            MockCredentialConnectionRepository(
                context.getBeanProvider(NamedParameterJdbcTemplate::class.java),
                actorRlsScope,
            )
        val transaction = TransactionTemplate(transactionManager)
        val owner = TestPeerUser.USER_ID
        asActor(owner) {
            transaction.executeWithoutResult { service.save(owner, "P" + "Q".repeat(19), "R" + "S".repeat(39), "8" + "6".repeat(8) + "5") }
        }
        val stored = requireNotNull(asActor(owner) { transaction.execute { service.summary(owner) } })
        assertEquals(null, stored.verifiedCashKrw)
        val proof =
            MockConnectionProof(
                cashKrw = 94_533_738,
                portfolioEquityKrw = 94_533_738 + 2_700_000,
                positions = listOf(MockConnectionPosition("055550", 45, 2_700_000)),
                positionsComplete = true,
            )
        // 연결 시도 잠금(60초)은 다른 테스트와 겹칠 수 있어 여기서는 관측 기록만 본다. 저장(STORED) 행도
        // 기록 대상이다 - 연결 확인은 기록한 뒤에 CONNECTED 로 넘어간다.
        asActor(owner) {
            transaction.executeWithoutResult { connection.recordBalance(owner, stored.accountId, proof) }
        }
        val confirmed = requireNotNull(asActor(owner) { transaction.execute { service.summary(owner) } })
        assertEquals(94_533_738L, confirmed.verifiedCashKrw)
        assertEquals(1, confirmed.verifiedPositionCount)
        assertNotNull(confirmed.verifiedAt)

        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { jdbc ->
            jdbc
                .prepareStatement(
                    """
                    select account_scope_hash, schema_version, source_version, position_count
                    from portfolio_balance_observations where owner_user_id = ? and source_ref is not null
                    order by observed_at desc limit 1
                    """.trimIndent(),
                ).use { statement ->
                    statement.setString(1, owner)
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        // 잔고 화면과 위험 잔고 투영은 계좌 식별자 앞 32자로 이 행을 찾는다.
                        assertEquals(stored.accountId.removePrefix("acct_") + "0".repeat(32), result.getString(1))
                        assertEquals("2", result.getString(2))
                        assertEquals("kis-mock-online-complete-v2", result.getString(3))
                        assertEquals(1, result.getInt(4))
                    }
                }
        }
        // 묶이지 않은 계좌나 다른 소유자의 계좌에는 관측을 남기지 못한다.
        asActor(owner) {
            assertThrows(Exception::class.java) {
                transaction.executeWithoutResult { connection.recordBalance(owner, "acct_" + "f".repeat(32), proof) }
            }
        }
        asActor("usr_demo_user") {
            assertThrows(Exception::class.java) {
                transaction.executeWithoutResult { connection.recordBalance(owner, stored.accountId, proof) }
            }
        }
    }

    @Test
    fun `automation status offers the credential bound account so arm targets the account orders use`() {
        val directory = prepareKeyDirectory()
        val service =
            MockCredentialSettingsService(
                context.getBeanProvider(NamedParameterJdbcTemplate::class.java),
                actorRlsScope,
                BrokerageCredentialCrypto(BrokerageKekFile(directory.toString())),
            )
        val transaction = TransactionTemplate(transactionManager)
        val owner = TestPeerUser.USER_ID
        asActor(owner) {
            transaction.executeWithoutResult { service.save(owner, "P" + "Q".repeat(19), "R" + "S".repeat(39), "8" + "6".repeat(8) + "5") }
        }
        val bound = requireNotNull(asActor(owner) { transaction.execute { service.summary(owner) } }).accountId
        val automation = context.getBean(com.capstone.decision.application.automation.AutomationService::class.java)
        val status = asActor(owner) { automation.statusV3(owner) }
        assertEquals(bound, status.accountId)
    }

    @Test
    fun userDisarmClearsBothMatchingAndStaleArmedSchedules() {
        val owner = "usr_demo_user"
        val automation = context.getBean(AutomationService::class.java)
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection
                .prepareStatement(
                    """
                    insert into automation_control(
                      user_id,control_state,version,brokerage_mode,account_id,principle_id,strategy_id,
                      baseline_account_digest,certification_status,kill_switch_active
                    ) values (?,'DISARMED',1,'KIS_MOCK',?,'prc_disarm_test','strategy_disarm_test',
                              repeat('a',64),'REQUIRED',false)
                    on conflict (user_id) do update set control_state='DISARMED',
                      version=automation_control.version+1,updated_at=statement_timestamp()
                    """.trimIndent(),
                ).use { statement ->
                    statement.setString(1, owner)
                    statement.setString(2, "acct_" + "d".repeat(32))
                    statement.executeUpdate()
                }
        }
        val initialVersion =
            requireNotNull(
                DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
                    connection
                        .prepareStatement(
                            "select version from automation_control where user_id = ?",
                        ).use { statement ->
                            statement.setString(1, owner)
                            statement.executeQuery().use { result ->
                                if (result.next()) result.getInt(1) else null
                            }
                        }
                },
            )
        val armedVersion = initialVersion + 1
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection
                .prepareStatement(
                    """
                    update automation_control
                    set control_state='ARMED',version=?,updated_at=statement_timestamp()
                    where user_id=?
                    """.trimIndent(),
                ).use { statement ->
                    statement.setInt(1, armedVersion)
                    statement.setString(2, owner)
                    assertEquals(1, statement.executeUpdate())
                }
        }

        fun insertSchedule(
            sessionDate: LocalDate,
            controlVersion: Int,
        ) {
            val scheduleId = "auto_sched_" + sessionDate.toString().replace("-", "").padStart(32, '0')
            val runAt = Timestamp.from(sessionDate.atTime(9, 30).atZone(ZoneId.of("Asia/Seoul")).toInstant())
            DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
                connection
                    .prepareStatement(
                        """
                        insert into automation_runtime_schedule(
                          schedule_id,user_id,session_date,control_version,schedule_state,run_at,created_at,updated_at
                        ) values (?,?,?,?,'ARMED',?,statement_timestamp(),statement_timestamp())
                        on conflict (user_id,session_date) do update set
                          control_version=excluded.control_version,schedule_state='ARMED',
                          run_at=excluded.run_at,updated_at=statement_timestamp()
                        """.trimIndent(),
                    ).use { statement ->
                        statement.setString(1, scheduleId)
                        statement.setString(2, owner)
                        statement.setObject(3, sessionDate)
                        statement.setInt(4, controlVersion)
                        statement.setTimestamp(5, runAt)
                        statement.executeUpdate()
                    }
            }
        }

        val currentSession = LocalDate.of(2030, 1, 2)
        val staleSession = LocalDate.of(2030, 1, 3)
        insertSchedule(currentSession, armedVersion)
        asActor(owner) {
            automation.disarm(owner, "schedule-stop-current", DisarmAutomationCommand(armedVersion))
        }
        val disarmedVersion =
            DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
                connection
                    .prepareStatement(
                        "select version from automation_control where user_id = ?",
                    ).use { statement ->
                        statement.setString(1, owner)
                        statement.executeQuery().use { result ->
                            check(result.next())
                            result.getInt(1)
                        }
                    }
            }
        assertEquals(armedVersion + 1, disarmedVersion)

        // Simulate the stale row that caused the live screen to report DISARMED while retaining
        // an old ARMED schedule. A fresh manual stop must clear it without incrementing control.
        insertSchedule(staleSession, armedVersion)
        asActor(owner) {
            automation.disarm(owner, "schedule-stop-stale", DisarmAutomationCommand(disarmedVersion))
        }
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection
                .prepareStatement(
                    """
                    select count(*) from automation_runtime_schedule
                    where user_id=? and session_date in (?,?) and schedule_state='DISARMED'
                    """.trimIndent(),
                ).use { statement ->
                    statement.setString(1, owner)
                    statement.setObject(2, currentSession)
                    statement.setObject(3, staleSession)
                    statement.executeQuery().use { result ->
                        check(result.next())
                        assertEquals(2, result.getInt(1))
                    }
                }
            connection
                .prepareStatement(
                    """
                    select count(*) from automation_schedule_disarm_events_v218
                    where owner_user_id=? and session_date in (?,?)
                    """.trimIndent(),
                ).use { statement ->
                    statement.setString(1, owner)
                    statement.setObject(2, currentSession)
                    statement.setObject(3, staleSession)
                    statement.executeQuery().use { result ->
                        check(result.next())
                        assertEquals(2, result.getInt(1))
                    }
                }
        }
    }

    @Test
    fun sameLastFourDifferentAccountNumbersNeverShareInternalAccountIds() {
        val directory = prepareKeyDirectory()
        val service =
            MockCredentialSettingsService(
                context.getBeanProvider(NamedParameterJdbcTemplate::class.java),
                actorRlsScope,
                BrokerageCredentialCrypto(BrokerageKekFile(directory.toString())),
            )
        val transaction = TransactionTemplate(transactionManager)
        val owner = TestPeerUser.USER_ID
        val key = "Z" + "C".repeat(19)
        val secret = "T" + "D".repeat(39)
        val firstAccountNo = "1111111234"
        val otherAccountNo = "9999991234"

        asActor(owner) {
            transaction.executeWithoutResult { service.save(owner, key, secret, firstAccountNo) }
        }
        val firstId = requireNotNull(asActor(owner) { transaction.execute { service.summary(owner) } }).accountId
        asActor(owner) {
            transaction.executeWithoutResult { service.save(owner, key, secret, otherAccountNo) }
        }
        val otherId = requireNotNull(asActor(owner) { transaction.execute { service.summary(owner) } }).accountId
        assertFalse(firstId == otherId)

        asActor(owner) {
            transaction.executeWithoutResult { service.save(owner, key, secret, firstAccountNo) }
        }
        val restoredId = requireNotNull(asActor(owner) { transaction.execute { service.summary(owner) } }).accountId
        assertEquals(firstId, restoredId)
    }

    private fun <T> asActor(
        userId: String,
        block: () -> T,
    ): T {
        val previous = SecurityContextHolder.getContext()
        val context = SecurityContextHolder.createEmptyContext()
        val actorRef = testActorCapabilityIssuer.actorRef(userId)
        context.authentication =
            UsernamePasswordAuthenticationToken(
                AppPrincipal(
                    userId,
                    "test-user",
                    if (userId == "usr_demo_user") "ADMIN" else "USER",
                    actorRef.securityVersion,
                    actorRef,
                ),
                null,
                emptyList(),
            )
        SecurityContextHolder.setContext(context)
        return try {
            block()
        } finally {
            SecurityContextHolder.setContext(previous)
        }
    }

    private fun prepareKeyDirectory(): Path {
        val directory = Files.createDirectory(root.resolve("brokerage"))
        Files.setPosixFilePermissions(
            directory,
            setOf(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE, PosixFilePermission.OWNER_EXECUTE),
        )
        val file = directory.resolve("brokerage-kek-v1.key")
        Files.write(file, testBrokerageKek)
        Files.setPosixFilePermissions(file, setOf(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE))
        return directory
    }

    companion object {
        private val testBrokerageKek = ByteArray(32).also(SecureRandom()::nextBytes)
        private val postgresImage =
            DockerImageName
                .parse("pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb")
                .asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            stablePostgresContainer(postgresImage)
                .withDatabaseName("decision_bound_mock")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")

        @DynamicPropertySource
        @JvmStatic
        fun postgresProperties(registry: DynamicPropertyRegistry) {
            registry.add("spring.datasource.url", postgres::getJdbcUrl)
            registry.add("spring.datasource.username") { "decision_app" }
            registry.add("spring.datasource.password") { "app-test" }
            registry.add("spring.flyway.user") { "flyway" }
            registry.add("spring.flyway.password") { "flyway-test" }
        }
    }
}
