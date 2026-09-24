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
        val accountId = "acct_" + HexFormat.of().formatHex(ByteArray(16).also(random::nextBytes))
        crypto.seal(ownerUserId, accountId, appKey, appSecret, accountNo).use { sealed ->
            val jdbc = jdbc()
            actorRlsScope.open(jdbc, ownerUserId, "PUT_MOCK_CREDENTIAL", "OWNER", ownerUserId)
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
