package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.flywaydb.core.Flyway
import org.junit.jupiter.api.BeforeAll
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.TestInstance
import org.junit.jupiter.api.assertThrows
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import java.nio.file.Path
import java.sql.Connection
import java.sql.DriverManager
import java.sql.SQLException
import java.time.OffsetDateTime
import kotlin.io.path.readText

/**
 * 앱 계층이 통과시키는 인용률을 저장 경계도 통과시키는지 **실제 PostgreSQL로** 확인한다.
 *
 * 왜 텍스트 검사로는 안 되는가. 기존 `*MigrationContractTest` 13개는 마이그레이션 SQL 파일을
 * `readText()` 해서 문자열을 찾는다. SQL 을 실행하지 않으므로 "앱이 허용하는 값을 DB 도
 * 허용한다"를 검증할 수 없고, 실제로 커밋 12593078 이 앱 하한만 없앤 것을 닷새 동안 아무
 * 테스트도 잡지 못했다. `RagV2RuntimeServiceTest` 는 `mockk<NamedParameterJdbcTemplate>()`
 * 이라 DB 규칙이 한 줄도 실행되지 않고, `RagV2ApiIntegrationTest` 에는 `citationCoverage`
 * 참조가 없다.
 *
 * 여기서 시험하는 것은 저장된 행의 모양을 지키는 테이블 CHECK 다. 함수 본문 전체를 태우려면
 * actor RLS 바인딩과 scope claim, AES-GCM blob 이 필요한데, 이번에 깨진 두 방어층 중 하나가
 * 정확히 이 CHECK 였고 함수만 고쳤을 때 여기서 다시 막혔을 것이다.
 */
@Testcontainers
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class RagV2CoverageFloorAlignmentIntegrationTest {
    @BeforeAll
    fun migrate() {
        Flyway
            .configure()
            .dataSource(postgres.jdbcUrl, "flyway", FLYWAY_PASSWORD)
            .locations("classpath:db/migration")
            .placeholders(
                mapOf(
                    "brokerageDbCapabilityTokenSha256" to
                        SpringApiIntegrationTestBase.TEST_BROKERAGE_DB_CAPABILITY_TOKEN_SHA256,
                ),
            ).javaMigrations(s21ActorTrustMigration())
            .load()
            .migrate()
    }

    @Test
    fun `V150은 앱이 하한을 없앤 뒤에도 저장되는 연결률을 그대로 받는다`() {
        // 앱은 EVIDENCE 0.8, EVIDENCE_WITH_REASONING 0.2 하한을 제거했다. 예전 CHECK 는
        // 0.2 미만을 거부했고 그래서 이 값들이 한 건도 저장되지 못했다.
        for ((index, coverage) in listOf(0.0, 0.1, 0.19, 0.5, 0.79, 1.0).withIndex()) {
            insertAnswered(answerId = "rag_${"c".repeat(28)}${"%04d".format(index)}", coverage = coverage)
        }
        flyway().use { connection ->
            connection.createStatement().use { statement ->
                statement.executeQuery("SELECT count(*) FROM public.rag_v2_answer_history").use { rows ->
                    rows.next()
                    assertThat(rows.getInt(1)).isEqualTo(6)
                }
            }
        }
    }

    @Test
    fun `연결률 범위 밖은 계속 거부한다`() {
        // 하한을 뺀 대신 범위 검사가 유일한 방어다. 이것까지 열리면 아무 값이나 저장된다.
        for (coverage in listOf(-0.01, 1.01)) {
            val error =
                assertThrows<SQLException> {
                    insertAnswered(answerId = "rag_${"d".repeat(28)}0001", coverage = coverage)
                }
            // 23514 는 CHECK 위반이다. SQLSTATE 를 고정하지 않으면 RLS 나 FK 로 실패해도
            // 초록불이 되어 정작 시험하려던 규칙이 사라진 것을 놓친다.
            assertThat(error.sqlState).isEqualTo("23514")
        }
    }

    @Test
    fun `MODEL_KNOWLEDGE 의 정확한 모양은 그대로 강제한다`() {
        // 하한과 달리 이 규칙은 근거 위조 방어다. 인용 0건인데 연결률이 0이 아니면 거짓이다.
        val error =
            assertThrows<SQLException> {
                insertAnswered(
                    answerId = "rag_${"e".repeat(28)}0001",
                    coverage = 0.5,
                    citationCount = 0,
                    flags = arrayOf("MODEL_KNOWLEDGE_ONLY"),
                )
            }
        assertThat(error.sqlState).isEqualTo("23514")
    }

    @Test
    fun `V150 은 함수와 테이블 CHECK 두 곳에서 함께 하한을 없앤다`() {
        // 한쪽만 고치면 다른 쪽에서 다시 막힌다. 두 변경이 같은 파일에 있는지 고정한다.
        val sql = Path.of("src/main/resources/db/migration/V150__rag_v2_coverage_floor_alignment.sql").readText()
        assertThat(sql).contains(
            "rag_v2_answer_history_status_result_check",
            "CREATE OR REPLACE FUNCTION public.persist_s4_9_strong_llm_history_v2",
            "p_citation_coverage NOT BETWEEN 0.0 AND 1.0",
            "citation_coverage BETWEEN 0.0 AND 1.0",
        )
        // 하한 자체가 어디에도 남아 있지 않아야 한다.
        assertThat(sql).doesNotContain("p_citation_coverage < 0.8", "p_citation_coverage < 0.2", "citation_coverage >= 0.8")
        // 근거 위조 방어는 유지된다.
        assertThat(sql).contains(
            "REASONING_SENTENCES_PRESENT",
            "MODEL_KNOWLEDGE_ONLY",
            "citation_count BETWEEN 1 AND 5",
            "canonicalize_s4_9_strong_llm_citations_v2",
            "SECURITY DEFINER",
        )
        assertThat(sql).doesNotContain("DROP FUNCTION", "DROP TABLE", "TRUNCATE")
    }

    private fun insertAnswered(
        answerId: String,
        coverage: Double,
        citationCount: Int = 1,
        flags: Array<String> = arrayOf("REASONING_SENTENCES_PRESENT"),
    ) {
        flyway().use { connection ->
            connection
                .prepareStatement(
                    """
                    INSERT INTO public.rag_v2_answer_history(
                      answer_id,owner_user_id,request_id,answer_mode,generation_status,citation_coverage,
                      retrieval_failure,guardrail_flags,public_corpus_version,private_overlay_state,kek_version,
                      wrap_nonce,wrapped_dek,wrap_tag,question_nonce,question_ciphertext,question_tag,
                      answer_nonce,answer_ciphertext,answer_tag,citation_count,created_at,expires_at
                    ) VALUES (
                      ?,?,?,'CONCISE','ANSWERED',?,
                      false,?,'immutable-v2-1','ABSENT','kek-v1',
                      ?,?,?,?,?,?,?,?,?,?,?,?
                    )
                    """.trimIndent(),
                ).use { statement ->
                    val now = OffsetDateTime.now()
                    statement.setString(1, answerId)
                    statement.setString(2, OWNER)
                    statement.setString(3, "req_${"1".repeat(32)}")
                    statement.setDouble(4, coverage)
                    statement.setArray(5, connection.createArrayOf("text", flags))
                    statement.setBytes(6, ByteArray(12))
                    statement.setBytes(7, ByteArray(32))
                    statement.setBytes(8, ByteArray(16))
                    statement.setBytes(9, ByteArray(12))
                    statement.setBytes(10, ByteArray(64))
                    statement.setBytes(11, ByteArray(16))
                    statement.setBytes(12, ByteArray(12))
                    statement.setBytes(13, ByteArray(64))
                    statement.setBytes(14, ByteArray(16))
                    statement.setInt(15, citationCount)
                    statement.setObject(16, now)
                    statement.setObject(17, now.plusDays(30))
                    statement.executeUpdate()
                }
        }
    }

    /**
     * `rag_v2_answer_history` 는 owner RLS 를 강제한다. 정책의 두 조건 중
     * `actor_rls_scope_is_open_v1()` 은 `session_user <> 'decision_app'` 일 때 참이므로
     * flyway 연결에서는 `app.actor_user_id` 만 맞추면 된다. 이걸 빼면 CHECK 를 시험하려던
     * 거부 케이스가 RLS 때문에 통과해 **잘못된 이유로 초록불**이 된다.
     */
    private fun flyway(): Connection =
        DriverManager.getConnection(postgres.jdbcUrl, "flyway", FLYWAY_PASSWORD).also { connection ->
            connection.createStatement().use { statement ->
                statement.execute("SET app.actor_user_id = '$OWNER'")
            }
        }

    companion object {
        private const val FLYWAY_PASSWORD = "flyway-test"
        private const val OWNER = "usr_demo_user"

        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            stablePostgresContainer(postgresImage)
                .withDatabaseName("decision_rag_v2_coverage")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")
    }
}
