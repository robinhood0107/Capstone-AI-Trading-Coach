package com.capstone.decision.application.rag

import org.slf4j.LoggerFactory
import org.springframework.stereotype.Service
import java.time.Clock
import java.time.Instant

@Service
class WorldNewsService(
    private val repository: WorldNewsRepository,
    private val clock: Clock = Clock.systemUTC(),
) {
    /** 빈 query는 최신 목록이며, 검색은 DB의 availableAt 경계를 넘지 않는다. */
    fun lookup(
        query: String,
        limit: Int,
    ): WorldNewsPage {
        val normalized = query.trim()
        if (
            normalized.length > 200 ||
            normalized.any { it.code in 0..8 || it.code in 11..12 || it.code in 14..31 || it.code == 127 } ||
            limit !in 1..50
        ) {
            throw WorldNewsValidationException()
        }
        val asOf = Instant.now(clock)
        return try {
            WorldNewsPage(
                items = repository.lookup(normalized, asOf, limit),
                collections = repository.collectionStatuses(),
                asOf = asOf,
            )
        } catch (exception: WorldNewsValidationException) {
            throw exception
        } catch (exception: RuntimeException) {
            // 사유를 삼키면 503 만 남아 매번 같은 조사를 처음부터 다시 해야 한다.
            // 실제로 수집기가 한 번에 8,986 건을 적재하는 동안 이 조회가 503 이 됐는데
            // 로그가 없어 원인을 DB 에서 되짚어야 했다. 응답은 그대로 두고 사유만 남긴다.
            log.warn("world-news lookup failed: {}", exception.toString())
            throw WorldNewsUnavailableException()
        }
    }

    private companion object {
        private val log = LoggerFactory.getLogger(WorldNewsService::class.java)
    }
}
