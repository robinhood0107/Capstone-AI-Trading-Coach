package com.capstone.decision.infrastructure.brokerage

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.application.brokerage.MockCredentialConnectionPort
import com.capstone.decision.application.security.ActorRlsScopePort
import org.springframework.beans.factory.ObjectProvider
import org.springframework.context.annotation.Profile
import org.springframework.dao.PessimisticLockingFailureException
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional

/** A successful read-only provider probe can advance only the exact row that was probed. */
@Service
@Profile("mars-full")
class MockCredentialConnectionService(
    private val settings: MockCredentialSettingsService,
    private val gatewayProvider: ObjectProvider<MockCredentialConnectionPort>,
    private val repository: MockCredentialConnectionRepository,
) {
    fun verify(
        ownerUserId: String,
        requestId: String,
    ) {
        val current = settings.summary(ownerUserId) ?: throw ApiException(ErrorCode.CONFLICT)
        if (current.state !in setOf("STORED", "CONNECTED", "CERTIFIED")) throw ApiException(ErrorCode.CONFLICT)
        val gateway = gatewayProvider.getIfAvailable() ?: throw ApiException(ErrorCode.BROKERAGE_UNAVAILABLE)
        try {
            repository.beginAttempt(ownerUserId, current.accountId, current.revision)
        } catch (_: PessimisticLockingFailureException) {
            throw ApiException(ErrorCode.CONFLICT)
        }
        gateway.verify(requestId, ownerUserId, current.accountId)
        try {
            repository.markConnected(ownerUserId, current.accountId, current.revision)
        } catch (_: PessimisticLockingFailureException) {
            throw ApiException(ErrorCode.CONFLICT)
        }
    }
}

@Service
@Profile("mars-full")
class MockCredentialConnectionRepository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val actorRlsScope: ActorRlsScopePort,
) {
    @Transactional
    fun beginAttempt(
        ownerUserId: String,
        accountId: String,
        revision: Long,
    ) {
        val jdbc = jdbcProvider.getIfAvailable() ?: error("BROKERAGE_CONNECTION_DATABASE_UNAVAILABLE")
        actorRlsScope.open(jdbc, ownerUserId, "BEGIN_MOCK_CONNECTION_ATTEMPT", "BROKER_CREDENTIAL", accountId)
        jdbc.query(
            "SELECT begin_bound_mock_connection_attempt_v1(:owner, :account, :revision)",
            mapOf("owner" to ownerUserId, "account" to accountId, "revision" to revision),
        ) { _, _ -> }
    }

    @Transactional
    fun markConnected(
        ownerUserId: String,
        accountId: String,
        revision: Long,
    ) {
        val jdbc = jdbcProvider.getIfAvailable() ?: error("BROKERAGE_CONNECTION_DATABASE_UNAVAILABLE")
        actorRlsScope.open(jdbc, ownerUserId, "MARK_MOCK_CREDENTIAL_CONNECTED", "BROKER_CREDENTIAL", accountId)
        val state =
            jdbc.queryForObject(
                "SELECT mark_bound_mock_broker_connected_v1(:owner, :account, :revision)",
                mapOf("owner" to ownerUserId, "account" to accountId, "revision" to revision),
                String::class.java,
            )
        check(state == "CONNECTED" || state == "CERTIFIED")
    }
}
