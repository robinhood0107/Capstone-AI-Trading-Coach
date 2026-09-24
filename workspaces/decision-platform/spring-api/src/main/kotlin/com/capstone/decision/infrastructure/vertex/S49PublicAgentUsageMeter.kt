package com.capstone.decision.infrastructure.vertex

import com.capstone.decision.infrastructure.security.PublicSurfaceMode
import org.springframework.beans.factory.ObjectProvider
import org.springframework.beans.factory.annotation.Value
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import java.nio.charset.StandardCharsets
import java.security.MessageDigest

/**
 * The Kotlin host owns the permit and records a conservative list-price estimate
 * before Python can open a provider socket. No prompt text is stored.
 */
@Component
@ConditionalOnProperty(name = ["app.s4-9.strong-llm.enabled"], havingValue = "true")
internal class S49PublicAgentUsageMeter(
    @Value("\${MARS_PUBLIC_SURFACE_MODE:LOCAL}") rawMode: String,
    @Value("\${P1_VERTEX_INPUT_MICROUSD_PER_TOKEN:3}") rawInputRate: String,
    @Value("\${P1_VERTEX_OUTPUT_MICROUSD_PER_TOKEN:17}") rawOutputRate: String,
    @Value("\${P1_VERTEX_GROUNDING_MICROUSD_PER_QUERY:14000}") rawGroundingRate: String,
    private val operatorUsageMeterProvider: ObjectProvider<OperatorAiUsageMeter>,
    private val strongLlmProperties: S49StrongLlmProperties,
    private val googleGroundingProperties: S49GoogleGroundingProperties,
) {
    private val mode = PublicSurfaceMode.valueOf(rawMode)
    private val inputRate = if (mode != PublicSurfaceMode.LOCAL) parseRate(rawInputRate, 3) else 0L
    private val outputRate = if (mode != PublicSurfaceMode.LOCAL) parseRate(rawOutputRate, 17) else 0L
    private val groundingRate = if (mode != PublicSurfaceMode.LOCAL) parseRate(rawGroundingRate, 14_000) else 0L

    fun record(
        ownerUserId: String,
        runId: String,
        plannedCallId: String,
        startFrameBytes: Int,
        contextBytesFromEvents: Long,
        priorProviderCalls: Int,
        googleSearchAttached: Boolean,
    ) {
        if (mode == PublicSurfaceMode.LOCAL) return
        check(mode != PublicSurfaceMode.DEMO || !googleSearchAttached) { "DEMO_AGENT_GOOGLE_SEARCH_FORBIDDEN" }
        if (runId.isBlank() || plannedCallId.isBlank()) return
        val maxGrossMicrousd =
            runCatching {
                quoteMaxGrossMicrousd(
                    startFrameBytes,
                    contextBytesFromEvents,
                    priorProviderCalls,
                    strongLlmProperties.maxOutputTokens,
                    inputRate,
                    outputRate,
                    if (googleSearchAttached) googleGroundingProperties.reservePerPrompt else 0,
                    groundingRate,
                )
            }.getOrNull() ?: return
        val reservationId = "aibr_" + sha256("$runId:$plannedCallId").take(32)
        val meter = operatorUsageMeterProvider.getIfAvailable() ?: return
        val source = if (mode == PublicSurfaceMode.FULL) "FULL_AGENT" else "DEMO_AGENT"
        val chargedOwner = ownerUserId.takeIf { mode == PublicSurfaceMode.FULL }
        runCatching {
            meter.recordGrossEstimate(reservationId, chargedOwner, source, "VERTEX", maxGrossMicrousd)
        }
    }

    private fun parseRate(
        raw: String,
        minimum: Long,
    ): Long {
        val rate = raw.toLongOrNull() ?: return minimum
        return rate.takeIf { it in minimum..1_000_000L } ?: minimum
    }

    private fun sha256(value: String): String =
        MessageDigest
            .getInstance("SHA-256")
            .digest(value.toByteArray(StandardCharsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
}

/** Text-only gRPC frames plus provider output cap form a conservative price proxy. */
internal fun quoteMaxGrossMicrousd(
    startFrameBytes: Int,
    contextBytesFromEvents: Long,
    priorProviderCalls: Int,
    outputTokenCap: Int,
    inputRate: Long,
    outputRate: Long,
    groundingQueryCap: Int,
    groundingRate: Long,
): Long {
    require(startFrameBytes in 1..262_144 && contextBytesFromEvents in 0..4_194_304)
    require(priorProviderCalls in 0..3 && outputTokenCap in 256..32_768)
    require(inputRate in 3L..1_000_000L && outputRate in 17L..1_000_000L)
    require(groundingQueryCap in 0..8 && groundingRate in 14_000L..1_000_000L)
    val inputProxy =
        startFrameBytes.toLong() + contextBytesFromEvents + 8_192L +
            priorProviderCalls.toLong() * outputTokenCap * 8L
    return inputProxy * inputRate + outputTokenCap * outputRate + groundingQueryCap * groundingRate
}
