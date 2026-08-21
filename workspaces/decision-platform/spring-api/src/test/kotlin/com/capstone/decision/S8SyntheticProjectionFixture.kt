package com.capstone.decision

import tools.jackson.databind.ObjectMapper
import java.security.MessageDigest
import java.time.Instant

object S8SyntheticProjectionFixture {
    const val ARTIFACT_ID = "artifact_s8_dashboard_00000001"
    const val RUN_ID = "demo_s8_dashboard_00000001"
    const val FILE_HASH = "sha256:9999999999999999999999999999999999999999999999999999999999999999"
    val asOf: Instant = Instant.parse("2026-08-22T00:00:00Z")
    val freshUntil: Instant = Instant.parse("2026-09-21T00:00:00Z")

    fun modelProjection(objectMapper: ObjectMapper): String =
        objectMapper.writeValueAsString(
            envelope(
                mapOf(
                    "runId" to RUN_ID,
                    "models" to
                        listOf(
                            mapOf(
                                "modelId" to "LIGHTGBM",
                                "status" to "ABSTAIN",
                                "metrics" to
                                    mapOf(
                                        "cagr" to null,
                                        "mdd" to null,
                                        "sharpe" to null,
                                        "sortino" to null,
                                        "var95" to null,
                                        "cvar95" to null,
                                    ),
                            ),
                        ),
                    "timeline" to emptyList<Any>(),
                    "sourceRunIds" to listOf(RUN_ID),
                ),
            ),
        )

    fun backtestProjection(objectMapper: ObjectMapper): String =
        objectMapper.writeValueAsString(
            envelope(mapOf("runId" to RUN_ID, "fixtureClass" to "SYNTHETIC_FAKE_E2E")),
        )

    fun sha256(value: String): String =
        "sha256:" + MessageDigest.getInstance("SHA-256").digest(value.toByteArray()).joinToString("") { "%02x".format(it) }

    private fun envelope(view: Map<String, Any?>) =
        mapOf(
            "success" to true,
            "requestId" to "req_s8_dashboard_fixture",
            "data" to
                mapOf(
                    "viewState" to "READY",
                    "asOf" to asOf.toString(),
                    "freshUntil" to freshUntil.toString(),
                    "evidenceMode" to "SYNTHETIC_DEMO",
                    "performanceClaimAllowed" to false,
                    "view" to view,
                ),
            "warnings" to emptyList<Any>(),
            "error" to null,
        )
}
