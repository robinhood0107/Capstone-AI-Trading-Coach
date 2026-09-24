package com.capstone.decision.infrastructure.brokerage

import com.capstone.decision.application.security.ActorRlsScopePort
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import org.springframework.transaction.annotation.Transactional
import java.time.LocalDate

data class MockCredentialCertificationAttempt(
    val certificationId: String,
    val sessionDate: LocalDate,
    val recovery: Boolean,
    val alreadyCertified: Boolean,
)

@Repository
class MockCredentialCertificationRepository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val actorRlsScope: ActorRlsScopePort,
) {
    @Transactional
    fun begin(
        ownerUserId: String,
        accountId: String,
        revision: Long,
        leaseTokenSha256: String,
    ): MockCredentialCertificationAttempt {
        val jdbc = jdbc()
        actorRlsScope.open(jdbc, ownerUserId, "BEGIN_MOCK_CERTIFICATION", "BROKER_CREDENTIAL", accountId)
        return jdbc
            .query(
                """
                SELECT * FROM begin_bound_mock_certification_v1(:owner,:account,:revision,:lease)
                """.trimIndent(),
                mapOf(
                    "owner" to ownerUserId,
                    "account" to accountId,
                    "revision" to revision,
                    "lease" to leaseTokenSha256,
                ),
            ) { row, _ ->
                MockCredentialCertificationAttempt(
                    certificationId = row.getString("certification_id"),
                    sessionDate = row.getDate("session_date").toLocalDate(),
                    recovery = row.getBoolean("recovery"),
                    alreadyCertified = row.getBoolean("already_certified"),
                )
            }.single()
    }

    @Transactional
    fun complete(
        ownerUserId: String,
        accountId: String,
        revision: Long,
        attempt: MockCredentialCertificationAttempt,
        leaseTokenSha256: String,
        receiptSha256: String,
        sessionDate: LocalDate,
        quoteCalls: Int,
        brokerageCalls: Int,
        tokenCalls: Int,
    ) {
        val jdbc = jdbc()
        actorRlsScope.open(jdbc, ownerUserId, "COMPLETE_MOCK_CERTIFICATION", "BROKER_CREDENTIAL", accountId)
        val state =
            jdbc.queryForObject(
                """
                SELECT complete_bound_mock_certification_v1(
                  :owner,:account,:revision,:attempt,:lease,:receipt,:session,:quote,:brokerage,:token
                )
                """.trimIndent(),
                mapOf(
                    "owner" to ownerUserId,
                    "account" to accountId,
                    "revision" to revision,
                    "attempt" to attempt.certificationId,
                    "lease" to leaseTokenSha256,
                    "receipt" to receiptSha256,
                    "session" to sessionDate,
                    "quote" to quoteCalls,
                    "brokerage" to brokerageCalls,
                    "token" to tokenCalls,
                ),
                String::class.java,
            )
        check(state == "CERTIFIED")
    }

    @Transactional
    fun finish(
        ownerUserId: String,
        accountId: String,
        revision: Long,
        attempt: MockCredentialCertificationAttempt,
        leaseTokenSha256: String,
        status: String,
        failureCode: String,
        sessionDate: LocalDate,
        quoteCalls: Int,
        brokerageCalls: Int,
        tokenCalls: Int,
    ) {
        val jdbc = jdbc()
        actorRlsScope.open(jdbc, ownerUserId, "FINISH_MOCK_CERTIFICATION", "BROKER_CREDENTIAL", accountId)
        val finished =
            jdbc.queryForObject(
                """
                SELECT finish_bound_mock_certification_v1(
                  :owner,:account,:revision,:attempt,:lease,:status,:failure,:session,:quote,:brokerage,:token
                )
                """.trimIndent(),
                mapOf(
                    "owner" to ownerUserId,
                    "account" to accountId,
                    "revision" to revision,
                    "attempt" to attempt.certificationId,
                    "lease" to leaseTokenSha256,
                    "status" to status,
                    "failure" to failureCode,
                    "session" to sessionDate,
                    "quote" to quoteCalls,
                    "brokerage" to brokerageCalls,
                    "token" to tokenCalls,
                ),
                String::class.java,
            )
        check(finished == status)
    }

    @Transactional
    fun acknowledgeRecovery(
        ownerUserId: String,
        accountId: String,
        revision: Long,
    ) {
        val jdbc = jdbc()
        actorRlsScope.open(
            jdbc,
            ownerUserId,
            "ACK_MOCK_CERTIFICATION_RECOVERY",
            "BROKER_CREDENTIAL",
            accountId,
        )
        val finished =
            jdbc.queryForObject(
                "SELECT acknowledge_bound_mock_certification_recovery_v1(:owner,:account,:revision)",
                mapOf("owner" to ownerUserId, "account" to accountId, "revision" to revision),
                String::class.java,
            )
        check(finished == "FAILED")
    }

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable() ?: error("MOCK_CREDENTIAL_CERTIFICATION_DATABASE_UNAVAILABLE")
}
