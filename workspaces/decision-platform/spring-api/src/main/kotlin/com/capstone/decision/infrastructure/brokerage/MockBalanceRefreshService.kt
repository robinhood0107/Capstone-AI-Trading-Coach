package com.capstone.decision.infrastructure.brokerage

import com.capstone.decision.application.brokerage.MockCredentialConnectionPort
import com.capstone.decision.application.brokerage.OwnerBalanceRefreshPort
import org.springframework.beans.factory.ObjectProvider
import org.springframework.context.annotation.Profile
import org.springframework.stereotype.Service
import java.time.Clock
import java.time.Duration
import java.time.Instant

/**
 * FULL 잔고 화면의 "지금 잔고". 소유자 자격증명에 묶인 계좌만, 마지막 관측이 [REFRESH_INTERVAL] 보다
 * 오래됐을 때만 KIS 잔고조회 한 번을 한다. 화면이 15초마다 다시 읽어도 KIS 호출은 분당 한 번을 넘지
 * 않는다. 호출은 연결 확인과 같은 읽기 전용 경로라 주문 회로를 건드리지 않는다.
 */
@Service
@Profile("mars-full")
class MockBalanceRefreshService(
    private val settings: MockCredentialSettingsService,
    private val gatewayProvider: ObjectProvider<MockCredentialConnectionPort>,
    private val repository: MockCredentialConnectionRepository,
    private val clock: Clock,
) : OwnerBalanceRefreshPort {
    override fun refreshIfStale(
        ownerUserId: String,
        accountId: String,
        requestId: String,
    ) {
        val current = settings.summary(ownerUserId) ?: return
        if (current.accountId != accountId || !current.connected) return
        val verifiedAt = current.verifiedAt?.let(Instant::parse)
        if (verifiedAt != null && verifiedAt.isAfter(clock.instant().minus(REFRESH_INTERVAL))) return
        val gateway = gatewayProvider.getIfAvailable() ?: return
        val proof = gateway.verify(requestId, ownerUserId, accountId) ?: return
        if (proof.positionsComplete) repository.recordBalance(ownerUserId, accountId, proof)
    }

    internal companion object {
        val REFRESH_INTERVAL: Duration = Duration.ofSeconds(60)
    }
}
