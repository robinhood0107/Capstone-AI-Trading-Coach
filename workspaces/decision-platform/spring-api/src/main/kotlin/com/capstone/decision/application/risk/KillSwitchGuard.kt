package com.capstone.decision.application.risk

/**
 * 판단과 주문 경계가 매 요청 DB gate를 읽게 하며 조회 실패도 통과시키지 않는다.
 */
class KillSwitchGuard(
    private val gatePort: KillSwitchGatePort,
) {
    fun check(): KillSwitchGate {
        val gate =
            try {
                gatePort.readGate()
            } catch (exception: KillSwitchUnavailableException) {
                throw exception
            } catch (exception: Exception) {
                throw KillSwitchUnavailableException(exception)
            }
        if (gate.active) {
            throw KillSwitchBlockedException()
        }
        return gate
    }
}
