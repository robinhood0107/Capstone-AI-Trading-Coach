package com.capstone.decision.infrastructure.brokerage

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.application.security.ActorRlsScopePort
import org.springframework.beans.factory.ObjectProvider
import org.springframework.context.annotation.Profile
import org.springframework.dao.PessimisticLockingFailureException
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional

/** DISCONNECTING retains encrypted recovery material until the user's pending ledger is clear. */
@Service
@Profile("mars-full")
class MockCredentialDisconnectService(
    private val settings: MockCredentialSettingsService,
    private val repository: MockCredentialDisconnectRepository,
) {
    fun disconnect(ownerUserId: String): String {
        val current = settings.summary(ownerUserId) ?: return "REMOVED"
        return try {
            repository.disconnect(ownerUserId, current.accountId, current.revision)
        } catch (_: PessimisticLockingFailureException) {
            throw ApiException(ErrorCode.CONFLICT)
        }
    }
}

@Service
@Profile("mars-full")
class MockCredentialDisconnectRepository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val actorRlsScope: ActorRlsScopePort,
    private val crypto: BrokerageCredentialCrypto,
) {
    @Transactional
    fun disconnect(
        ownerUserId: String,
        accountId: String,
        revision: Long,
    ): String {
        val jdbc = jdbcProvider.getIfAvailable() ?: error("BROKERAGE_CREDENTIAL_DATABASE_UNAVAILABLE")
        actorRlsScope.open(jdbc, ownerUserId, "DISCONNECT_MOCK_CREDENTIAL", "BROKER_CREDENTIAL", accountId)
        val envelope =
            jdbc.query(
                "SELECT * FROM p1_read_mock_credential_identity_envelope_v218(:owner)",
                mapOf("owner" to ownerUserId),
            ) { row, _ ->
                BoundMockCredentialEnvelope(
                    accountId = row.getString("account_id"),
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
        envelope.use { current ->
            check(current.accountId == accountId && current.revision == revision)
            crypto.open(ownerUserId, accountId, current.sealed).use { opened ->
                val fingerprint = crypto.accountIdentityFingerprint(ownerUserId, opened.accountNo)
                check(
                    jdbc.queryForObject(
                        """
                        SELECT p1_register_mock_account_identity_before_disconnect_v218(
                          :owner,:account,:fingerprint
                        )
                        """.trimIndent(),
                        mapOf("owner" to ownerUserId, "account" to accountId, "fingerprint" to fingerprint),
                        Boolean::class.java,
                    ) == true,
                )
            }
        }
        val result =
            jdbc.queryForObject(
                "SELECT disconnect_bound_mock_broker_credential_v1(:owner, :account, :revision)",
                mapOf("owner" to ownerUserId, "account" to accountId, "revision" to revision),
                String::class.java,
            )
        check(result == "DISCONNECTING" || result == "REMOVED")
        return result
    }
}
