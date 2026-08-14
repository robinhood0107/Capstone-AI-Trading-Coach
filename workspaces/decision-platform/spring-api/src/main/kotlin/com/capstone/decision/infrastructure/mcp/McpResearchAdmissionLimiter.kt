package com.capstone.decision.infrastructure.mcp

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import java.time.Clock
import java.time.Instant
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Semaphore

/** 외부 MCP client의 반복 research 호출은 owner/client별 15분 cap과 user/global read concurrency를 함께 적용한다. */
@Component
@ConditionalOnProperty(name = ["app.rag-v2.web.enabled"], havingValue = "true")
class McpResearchAdmissionLimiter(
    private val properties: RagWebToolProperties,
    private val clock: Clock = Clock.systemUTC(),
) {
    private val windows = ConcurrentHashMap<String, Window>()
    private val globalReads = Semaphore(properties.externalResearchGlobalParallelReads, true)

    init {
        properties.validate()
    }

    fun acquireSearch(caller: McpCaller) {
        val window = current(caller)
        synchronized(window) {
            require(window.searches < properties.externalResearchMaxSearches)
            window.searches += 1
        }
    }

    fun <T> withRead(
        caller: McpCaller,
        block: () -> T,
    ): T {
        val window = current(caller)
        require(globalReads.tryAcquire())
        if (!window.userReads.tryAcquire()) {
            globalReads.release()
            throw IllegalStateException("MCP user read concurrency exhausted")
        }
        try {
            synchronized(window) {
                require(window.reads < properties.externalResearchMaxReads)
                window.reads += 1
            }
            return block()
        } finally {
            window.userReads.release()
            globalReads.release()
        }
    }

    private fun current(caller: McpCaller): Window {
        val now = clock.instant()
        val key = "${caller.ownerUserId}|${caller.oauthClientId}"
        synchronized(windows) {
            windows.entries.removeIf {
                !now.isBefore(it.value.startedAt.plusSeconds(properties.externalResearchWindowMinutes * 60L))
            }
            windows[key]?.let { return it }
            require(windows.size < properties.externalResearchMaxTotalContexts)
            return Window(now, Semaphore(properties.externalResearchUserParallelReads, true)).also { windows[key] = it }
        }
    }

    private data class Window(
        val startedAt: Instant,
        val userReads: Semaphore,
        var searches: Int = 0,
        var reads: Int = 0,
    )
}
