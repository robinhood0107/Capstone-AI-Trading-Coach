package com.capstone.decision.application.risk

/**
 * source I/O의 absolute evaluation deadline을 공유하는 application 경계다.
 * 구현은 timeout·동시성 초과 시 새 physical call을 만들지 않고 전달받은 typed fallback을 반환한다.
 */
interface EvaluationSourceCallCoordinator {
    fun <T> call(
        evaluationDeadlineNanos: Long,
        fallback: T,
        operation: () -> T,
    ): T
}

/**
 * 순수 단위 테스트나 명시적인 offline 조립에서만 사용하는 direct coordinator다.
 * production bean graph는 bounded infrastructure 구현을 반드시 주입한다.
 */
object DirectEvaluationSourceCallCoordinator : EvaluationSourceCallCoordinator {
    override fun <T> call(
        evaluationDeadlineNanos: Long,
        fallback: T,
        operation: () -> T,
    ): T = operation()
}
