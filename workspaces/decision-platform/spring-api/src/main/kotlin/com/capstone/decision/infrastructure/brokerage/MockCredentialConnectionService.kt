package com.capstone.decision.infrastructure.brokerage

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.application.brokerage.MockConnectionProof
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
        // 연결 확인은 KIS 잔고조회 한 번이다. 실패하면(앱 키 거부·계좌 불일치·KIS 장애) 여기서 예외가 나가고
        // 행은 CONNECTED 로 넘어가지 않는다.
        val proof = gateway.verify(requestId, ownerUserId, current.accountId)
        // 읽은 잔고를 이 계좌의 온라인 관측으로 남긴다. 잔고 화면과 무장(위험 잔고 투영)이 같은 행을 읽는다.
        // 보유 종목이 한 쪽을 넘으면 불완전한 잔고라 위험 근거로 남기지 않는다.
        if (proof != null && proof.positionsComplete) {
            repository.recordBalance(ownerUserId, current.accountId, proof)
        }
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
    fun recordBalance(
        ownerUserId: String,
        accountId: String,
        proof: MockConnectionProof,
    ) {
        val jdbc = jdbcProvider.getIfAvailable() ?: error("BROKERAGE_CONNECTION_DATABASE_UNAVAILABLE")
        actorRlsScope.open(jdbc, ownerUserId, "RECORD_MOCK_BALANCE_OBSERVATION", "BROKER_CREDENTIAL", accountId)
        val positions =
            proof.positions.joinToString(prefix = "[", postfix = "]") {
                """{"symbol":"${it.symbol}","quantity":${it.quantity},"marketValueKrw":${it.marketValueKrw}}"""
            }
        jdbc.queryForObject(
            """
            SELECT record_bound_mock_balance_observation_v1(
              :owner, :account, :cash, :equity, CAST(:positions AS jsonb)
            )
            """.trimIndent(),
            mapOf(
                "owner" to ownerUserId,
                "account" to accountId,
                "cash" to proof.cashKrw,
                "equity" to proof.portfolioEquityKrw,
                "positions" to positions,
            ),
            String::class.java,
        )
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
