package com.capstone.decision.infrastructure.risk

import com.capstone.decision.application.risk.EvaluationSourceCallCoordinator
import com.capstone.decision.domain.risk.EvaluationBounds
import jakarta.annotation.PreDestroy
import org.slf4j.MDC
import org.springframework.security.core.context.SecurityContextHolder
import org.springframework.stereotype.Component
import java.util.concurrent.ExecutionException
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.SynchronousQueue
import java.util.concurrent.ThreadFactory
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
import java.util.concurrent.atomic.AtomicInteger
import kotlin.math.min

/**
 * queue 없는 8-worker 경계에서 source당 500ms와 전체 evaluation 900ms의 남은 예산을 함께 강제한다.
 * timeout task는 interrupt하고 caller는 typed unavailable로 진행하되 worker 수 이상 physical call은 만들지 않는다.
 */
@Component
class BoundedEvaluationSourceCallCoordinator :
    EvaluationSourceCallCoordinator,
    AutoCloseable {
    private val executor =
        ThreadPoolExecutor(
            EvaluationBounds.MAX_CONCURRENCY,
            EvaluationBounds.MAX_CONCURRENCY,
            0L,
            TimeUnit.MILLISECONDS,
            SynchronousQueue(),
            SourceThreadFactory(),
            ThreadPoolExecutor.AbortPolicy(),
        )

    override fun <T> call(
        evaluationDeadlineNanos: Long,
        fallback: T,
        operation: () -> T,
    ): T {
        val remainingNanos = evaluationDeadlineNanos - System.nanoTime()
        if (remainingNanos <= 0) {
            return fallback
        }
        val timeoutNanos = min(EvaluationBounds.SOURCE_DEADLINE.toNanos(), remainingNanos)
        val callerMdc = MDC.getCopyOfContextMap()
        val callerAuthentication = SecurityContextHolder.getContext().authentication
        val future =
            try {
                executor.submit<T> {
                    val workerMdc = MDC.getCopyOfContextMap()
                    val workerContext = SecurityContextHolder.getContext()
                    try {
                        if (callerMdc == null) {
                            MDC.clear()
                        } else {
                            MDC.setContextMap(callerMdc)
                        }
                        val delegatedContext = SecurityContextHolder.createEmptyContext()
                        delegatedContext.authentication = callerAuthentication
                        SecurityContextHolder.setContext(delegatedContext)
                        operation()
                    } finally {
                        SecurityContextHolder.setContext(workerContext)
                        if (workerMdc == null) {
                            MDC.clear()
                        } else {
                            MDC.setContextMap(workerMdc)
                        }
                    }
                }
            } catch (_: RejectedExecutionException) {
                return fallback
            }
        return try {
            future.get(timeoutNanos, TimeUnit.NANOSECONDS)
        } catch (_: TimeoutException) {
            future.cancel(true)
            fallback
        } catch (_: InterruptedException) {
            future.cancel(true)
            Thread.currentThread().interrupt()
            fallback
        } catch (exception: ExecutionException) {
            throw exception.cause.asRuntimeFailure()
        }
    }

    @PreDestroy
    override fun close() {
        executor.shutdownNow()
    }

    private fun Throwable?.asRuntimeFailure(): RuntimeException =
        when (this) {
            is RuntimeException -> this
            is Error -> throw this
            null -> IllegalStateException("Source call failed without a cause.")
            else -> IllegalStateException("Source call failed.", this)
        }

    private class SourceThreadFactory : ThreadFactory {
        private val sequence = AtomicInteger()

        override fun newThread(task: Runnable): Thread =
            Thread(task, "decision-source-${sequence.incrementAndGet()}").apply {
                isDaemon = true
            }
    }
}
