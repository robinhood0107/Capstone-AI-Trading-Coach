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
) {
    @Transactional
    fun disconnect(
        ownerUserId: String,
        accountId: String,
        revision: Long,
    ): String {
        val jdbc = jdbcProvider.getIfAvailable() ?: error("BROKERAGE_CREDENTIAL_DATABASE_UNAVAILABLE")
        actorRlsScope.open(jdbc, ownerUserId, "DISCONNECT_MOCK_CREDENTIAL", "BROKER_CREDENTIAL", accountId)
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
