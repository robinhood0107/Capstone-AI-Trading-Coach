package com.capstone.decision.infrastructure.brokerage

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.application.brokerage.MockCredentialCertificationPort
import com.capstone.decision.application.brokerage.MockCredentialCertificationStatus
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.ObjectProvider
import org.springframework.context.annotation.Profile
import org.springframework.dao.PessimisticLockingFailureException
import org.springframework.stereotype.Service
import java.security.MessageDigest
import java.security.SecureRandom
import java.time.LocalDate

data class MockCredentialCertificationOutcome(
    val status: String,
    val certificationId: String?,
    val sessionDate: String?,
    val quoteCalls: Int,
    val brokerageCalls: Int,
    val tokenCalls: Int,
    val failureCode: String?,
)

/** One explicit owner click runs the fixed mock order test before the account can be armed. */
@Service
@Profile("mars-full")
class MockCredentialCertificationService(
    private val settings: MockCredentialSettingsService,
    private val certificationPortProvider: ObjectProvider<MockCredentialCertificationPort>,
    private val repository: MockCredentialCertificationRepository,
) {
    fun certify(
        ownerUserId: String,
        requestId: String,
    ): MockCredentialCertificationOutcome {
        val current = settings.summary(ownerUserId) ?: throw ApiException(ErrorCode.CONFLICT)
        if (current.certified) {
            return MockCredentialCertificationOutcome(
                status = "PASS",
                certificationId = null,
                sessionDate = current.certificationSessionDate,
                quoteCalls = 0,
                brokerageCalls = 0,
                tokenCalls = 0,
                failureCode = null,
            )
        }
        if (current.state != "CONNECTED") throw ApiException(ErrorCode.CONFLICT)
        val leaseTokenSha256 = leaseTokenSha256()
        val attempt =
            try {
                repository.begin(ownerUserId, current.accountId, current.revision, leaseTokenSha256)
            } catch (_: PessimisticLockingFailureException) {
                throw ApiException(ErrorCode.CONFLICT)
            }
        if (attempt.alreadyCertified) {
            return MockCredentialCertificationOutcome(
                status = "PASS",
                certificationId = attempt.certificationId,
                sessionDate = attempt.sessionDate.toString(),
                quoteCalls = 0,
                brokerageCalls = 0,
                tokenCalls = 0,
                failureCode = null,
            )
        }
        val gateway = certificationPortProvider.getIfAvailable()
        if (gateway == null) {
            return finishFailure(
                ownerUserId,
                current,
                attempt,
                leaseTokenSha256,
                "FAILED",
                "PROVIDER_FAILED",
                attempt.sessionDate,
                0,
                0,
                0,
            )
        }
        val proof =
            try {
                gateway.certify(
                    requestId = requestId,
                    ownerUserId = ownerUserId,
                    accountId = current.accountId,
                    certificationId = attempt.certificationId,
                    sessionDate = attempt.sessionDate.toString(),
                    recovery = attempt.recovery,
                )
            } catch (_: Exception) {
                // RPC loss is ambiguous after order submission. Keep the exact account/revision locked
                // until the same certification ID reconciles its encrypted KIS order reference.
                return finishFailure(
                    ownerUserId,
                    current,
                    attempt,
                    leaseTokenSha256,
                    "RECOVERY_REQUIRED",
                    "TEST_ORDER_UNCERTAIN",
                    attempt.sessionDate,
                    0,
                    0,
                    0,
                )
            }
        val sessionDate = runCatching { LocalDate.parse(proof.sessionDate) }.getOrNull()
        if (sessionDate != attempt.sessionDate) {
            return finishFailure(
                ownerUserId,
                current,
                attempt,
                leaseTokenSha256,
                "RECOVERY_REQUIRED",
                "TEST_ORDER_UNCERTAIN",
                attempt.sessionDate,
                proof.quoteCalls,
                proof.brokerageCalls,
                proof.tokenCalls,
            )
        }
        if (proof.status != MockCredentialCertificationStatus.PASS) {
            return finishFailure(
                ownerUserId,
                current,
                attempt,
                leaseTokenSha256,
                if (proof.status == MockCredentialCertificationStatus.RECOVERY_REQUIRED) {
                    "RECOVERY_REQUIRED"
                } else {
                    "FAILED"
                },
                proof.failureCode.ifBlank { "PROVIDER_FAILED" },
                sessionDate,
                proof.quoteCalls,
                proof.brokerageCalls,
                proof.tokenCalls,
            )
        }
        if (!RECEIPT_SHA256.matches(proof.receiptSha256)) {
            return finishFailure(
                ownerUserId,
                current,
                attempt,
                leaseTokenSha256,
                "RECOVERY_REQUIRED",
                "TEST_ORDER_UNCERTAIN",
                sessionDate,
                proof.quoteCalls,
                proof.brokerageCalls,
                proof.tokenCalls,
            )
        }
        try {
            repository.complete(
                ownerUserId = ownerUserId,
                accountId = current.accountId,
                revision = current.revision,
                attempt = attempt,
                leaseTokenSha256 = leaseTokenSha256,
                receiptSha256 = proof.receiptSha256,
                sessionDate = sessionDate,
                quoteCalls = proof.quoteCalls,
                brokerageCalls = proof.brokerageCalls,
                tokenCalls = proof.tokenCalls,
            )
        } catch (_: PessimisticLockingFailureException) {
            LOGGER.warn("mock_credential_certification_commit_conflict")
            return finishFailure(
                ownerUserId,
                current,
                attempt,
                leaseTokenSha256,
                "RECOVERY_REQUIRED",
                "TEST_ORDER_UNCERTAIN",
                sessionDate,
                proof.quoteCalls,
                proof.brokerageCalls,
                proof.tokenCalls,
            )
        }
        return MockCredentialCertificationOutcome(
            status = "PASS",
            certificationId = attempt.certificationId,
            sessionDate = sessionDate.toString(),
            quoteCalls = proof.quoteCalls,
            brokerageCalls = proof.brokerageCalls,
            tokenCalls = proof.tokenCalls,
            failureCode = null,
        )
    }

    fun acknowledgeRecovery(ownerUserId: String) {
        val current = settings.summary(ownerUserId) ?: throw ApiException(ErrorCode.CONFLICT)
        if (current.state != "CONNECTED" || current.certificationStatus != "RECOVERY_REQUIRED") {
            throw ApiException(ErrorCode.CONFLICT)
        }
        try {
            repository.acknowledgeRecovery(ownerUserId, current.accountId, current.revision)
        } catch (_: PessimisticLockingFailureException) {
            throw ApiException(ErrorCode.CONFLICT)
        }
    }

    private fun finishFailure(
        ownerUserId: String,
        current: MockCredentialSummary,
        attempt: MockCredentialCertificationAttempt,
        leaseTokenSha256: String,
        status: String,
        failureCode: String,
        sessionDate: LocalDate,
        quoteCalls: Int,
        brokerageCalls: Int,
        tokenCalls: Int,
    ): MockCredentialCertificationOutcome {
        repository.finish(
            ownerUserId = ownerUserId,
            accountId = current.accountId,
            revision = current.revision,
            attempt = attempt,
            leaseTokenSha256 = leaseTokenSha256,
            status = status,
            failureCode = failureCode,
            sessionDate = sessionDate,
            quoteCalls = quoteCalls.coerceIn(0, 1),
            brokerageCalls = brokerageCalls.coerceIn(0, 7),
            tokenCalls = tokenCalls.coerceIn(0, 1),
        )
        return MockCredentialCertificationOutcome(
            status = status,
            certificationId = attempt.certificationId,
            sessionDate = sessionDate.toString(),
            quoteCalls = quoteCalls.coerceIn(0, 1),
            brokerageCalls = brokerageCalls.coerceIn(0, 7),
            tokenCalls = tokenCalls.coerceIn(0, 1),
            failureCode = failureCode,
        )
    }

    private fun leaseTokenSha256(): String {
        val random = ByteArray(32).also(SecureRandom()::nextBytes)
        return try {
            "sha256:" +
                MessageDigest
                    .getInstance("SHA-256")
                    .digest(random)
                    .joinToString("") { "%02x".format(it) }
        } finally {
            random.fill(0)
        }
    }

    private companion object {
        val LOGGER = LoggerFactory.getLogger(MockCredentialCertificationService::class.java)
        val RECEIPT_SHA256 = Regex("^[0-9a-f]{64}$")
    }
}
