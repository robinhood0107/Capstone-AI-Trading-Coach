package com.capstone.decision.infrastructure.brokerage

import com.capstone.decision.application.security.ActorRlsScopePort
import org.springframework.beans.factory.ObjectProvider
import org.springframework.context.annotation.Profile
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional
import java.security.SecureRandom
import java.util.HexFormat

data class MockCredentialSummary(
    val accountId: String,
    val state: String,
    val revision: Long,
    val appKeyLast4: String,
    val accountNoLast4: String,
    val connected: Boolean,
    val certified: Boolean,
    val certificationStatus: String,
    val certificationFailureCode: String?,
    val certificationSessionDate: String?,
    /** 마지막 연결 확인이 KIS 에서 읽은 예수금. 연결 확인 전이면 null 이다. */
    val verifiedCashKrw: Long? = null,
    val verifiedPositionCount: Int? = null,
    val verifiedAt: String? = null,
)

class BoundMockCredentialEnvelope(
    val accountId: String,
    val state: String,
    val revision: Long,
    val sealed: SealedBrokerageCredential,
) : AutoCloseable {
    override fun close() = sealed.close()

    override fun toString(): String = "BoundMockCredentialEnvelope(<redacted>)"
}

/**
 * Stores exactly one owner-bound KIS_MOCK envelope. A saved key is neither connected nor order-ready.
 * The online reader and certification path must join this account ID before the public gate opens.
 */
@Service
@Profile("mars-full")
class MockCredentialSettingsService(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val actorRlsScope: ActorRlsScopePort,
    private val crypto: BrokerageCredentialCrypto,
    private val random: SecureRandom = SecureRandom(),
) {
    @Transactional
    fun save(
        ownerUserId: String,
        appKey: String,
        appSecret: String,
        accountNo: String,
    ) {
        val jdbc = jdbc()
        actorRlsScope.open(jdbc, ownerUserId, "PUT_MOCK_CREDENTIAL", "OWNER", ownerUserId)
        // 같은 실계좌를 다시 저장하면 기존 계좌 식별자를 그대로 쓴다. 새 무작위 식별자를 쓰면 그 계좌의
        // 주문·보유·자동매매 이력과 잔고 관측이 옛 식별자에 남아 화면과 무장에서 사라진다.
        // 끝 4자리가 다른 계좌는 언제나 새 식별자다(다른 실계좌의 이력을 섞지 않는다).
        val reused =
            jdbc.queryForObject(
                "SELECT resolve_bound_mock_account_reuse_v1(:owner, :accountLast4)",
                mapOf("owner" to ownerUserId, "accountLast4" to accountNo.takeLast(4)),
                String::class.java,
            )
        val accountId = reused ?: ("acct_" + HexFormat.of().formatHex(ByteArray(16).also(random::nextBytes)))
        crypto.seal(ownerUserId, accountId, appKey, appSecret, accountNo).use { sealed ->
            jdbc.query(
                """
                SELECT put_bound_mock_broker_credential_v2(
                  :owner, :account, :version, :wrapNonce, :wrappedDek, :wrapTag,
                  :secretNonce, :ciphertext, :secretTag, :appLast4, :accountLast4
                )
                """.trimIndent(),
                mapOf(
                    "owner" to ownerUserId,
                    "account" to accountId,
                    "version" to sealed.kekVersion,
                    "wrapNonce" to sealed.wrapNonce,
                    "wrappedDek" to sealed.wrappedDek,
                    "wrapTag" to sealed.wrapTag,
                    "secretNonce" to sealed.secretNonce,
                    "ciphertext" to sealed.secretCiphertext,
                    "secretTag" to sealed.secretTag,
                    "appLast4" to sealed.appKeyLast4,
                    "accountLast4" to sealed.accountNoLast4,
                ),
            ) { _, _ -> }
        }
    }

    @Transactional
    fun summary(ownerUserId: String): MockCredentialSummary? {
        val jdbc = jdbc()
        actorRlsScope.open(jdbc, ownerUserId, "READ_MOCK_CREDENTIAL_SUMMARY", "OWNER", ownerUserId)
        // 연결 확인이 남긴 최근 잔고 요약. 계좌번호 전체·보유 종목 목록은 싣지 않는다.
        val confirmation =
            jdbc
                .query(
                    "SELECT cash_krw, position_count, observed_at FROM read_bound_mock_balance_confirmation_v1(:owner)",
                    mapOf("owner" to ownerUserId),
                ) { row, _ ->
                    Triple(
                        row.getLong("cash_krw"),
                        row.getInt("position_count"),
                        row.getObject("observed_at", java.time.OffsetDateTime::class.java).toInstant().toString(),
                    )
                }.singleOrNull()
        return jdbc
            .query(
                "SELECT * FROM read_bound_mock_broker_summary_v3(:owner)",
                mapOf("owner" to ownerUserId),
            ) { row, _ ->
                val state = row.getString("credential_state")
                MockCredentialSummary(
                    accountId = row.getString("account_id"),
                    state = state,
                    revision = row.getLong("revision"),
                    appKeyLast4 = row.getString("app_key_last4"),
                    accountNoLast4 = row.getString("account_no_last4"),
                    connected = state == "CONNECTED" || state == "CERTIFIED",
                    certified = state == "CERTIFIED",
                    certificationStatus = row.getString("certification_status"),
                    certificationFailureCode = row.getString("certification_failure_code"),
                    certificationSessionDate = row.getDate("certification_session_date")?.toLocalDate()?.toString(),
                    verifiedCashKrw = confirmation?.first,
                    verifiedPositionCount = confirmation?.second,
                    verifiedAt = confirmation?.third,
                )
            }.singleOrNull()
    }

    /** Returns only the current owner's encrypted row; caller must close its byte arrays. */
    @Transactional
    fun resolveEnvelope(
        ownerUserId: String,
        accountId: String,
    ): BoundMockCredentialEnvelope {
        val jdbc = jdbc()
        actorRlsScope.open(jdbc, ownerUserId, "READ_MOCK_CREDENTIAL_ENVELOPE", "BROKER_CREDENTIAL", accountId)
        return jdbc
            .query(
                "SELECT * FROM read_bound_mock_broker_envelope_v3(:owner, :account)",
                mapOf("owner" to ownerUserId, "account" to accountId),
            ) { row, _ ->
                BoundMockCredentialEnvelope(
                    accountId = accountId,
                    state = row.getString("credential_state"),
                    revision = row.getLong("revision"),
                    sealed =
                        SealedBrokerageCredential(
                            kekVersion = row.getString("kek_version"),
                            wrapNonce = row.getBytes("wrap_nonce"),
                            wrappedDek = row.getBytes("wrapped_dek"),
                            wrapTag = row.getBytes("wrap_tag"),
                            secretNonce = row.getBytes("secret_nonce"),
                            secretCiphertext = row.getBytes("secret_ciphertext"),
                            secretTag = row.getBytes("secret_tag"),
                            appKeyLast4 = row.getString("app_key_last4"),
                            accountNoLast4 = row.getString("account_no_last4"),
                        ),
                )
            }.singleOrNull() ?: error("BROKERAGE_CREDENTIAL_UNAVAILABLE")
    }

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable() ?: throw IllegalStateException("MOCK_CREDENTIAL_DATABASE_UNAVAILABLE")
}
