package com.capstone.decision.infrastructure.grpc

import com.capstone.decision.application.brokerage.BrokerageUnavailableException
import com.capstone.decision.infrastructure.brokerage.MockCredentialSettingsService
import io.github.resilience4j.circuitbreaker.CallNotPermittedException
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.springframework.beans.factory.support.StaticListableBeanFactory

class GrpcBrokerageAdapterCircuitTest {
    private val registry = CircuitBreakerRegistry.ofDefaults()
    private val adapter =
        GrpcBrokerageAdapter(
            BrokerageGrpcProperties(
                enabled = true,
                target = "127.0.0.1:1",
                sharedSecret = "s".repeat(40),
                deadlineMillis = 2_000,
            ),
            registry,
            StaticListableBeanFactory().getBeanProvider(MockCredentialSettingsService::class.java),
        )

    @Test
    fun `an open circuit is brokerage unavailable instead of an unhandled 500`() {
        registry.circuitBreaker("kisMockBrokerage").transitionToOpenState()
        val error = assertThrows<BrokerageUnavailableException> { adapter.guarded { 1 } }
        assertEquals(CallNotPermittedException::class.java, error.cause?.javaClass)
        adapter.close()
    }

    @Test
    fun `one user's key check never consults or trips the shared circuit`() {
        val breaker = registry.circuitBreaker("kisMockBrokerage")
        breaker.transitionToOpenState()
        // 회로가 열려 있어도 연결 확인은 공급자에게 직접 묻고, 실패는 503 부류로 끝난다.
        val error =
            assertThrows<BrokerageUnavailableException> {
                adapter.verify("req_test", "usr_test", "acct_${"a".repeat(32)}")
            }
        assertFalse(error.cause is CallNotPermittedException)
        breaker.transitionToClosedState()
        repeat(5) {
            runCatching { adapter.verify("req_test", "usr_test", "acct_${"a".repeat(32)}") }
        }
        assertEquals(0, breaker.metrics.numberOfFailedCalls)
        adapter.close()
    }
}
