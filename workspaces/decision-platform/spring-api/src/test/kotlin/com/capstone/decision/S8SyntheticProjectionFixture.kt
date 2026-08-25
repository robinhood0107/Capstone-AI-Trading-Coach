package com.capstone.decision

import tools.jackson.databind.ObjectMapper
import java.security.MessageDigest
import java.time.Instant

object S8SyntheticProjectionFixture {
    const val ARTIFACT_ID = "artifact_s8_0ed32aac66088e495ae853bb"
    const val RUN_ID = "demo_s8_fake_e2e_0001"
    const val FILE_HASH = "sha256:0ed32aac66088e495ae853bbac98a35b2c4a22420138bdd58dcdbbb0d9d8ad02"
    val asOf: Instant = Instant.parse("2026-08-22T00:00:00Z")
    val freshUntil: Instant = Instant.parse("2026-09-21T00:00:00Z")

    fun modelProjection(objectMapper: ObjectMapper): String = projection(objectMapper, "model-evaluation.json")

    fun backtestProjection(objectMapper: ObjectMapper): String = projection(objectMapper, "backtest.json")

    fun sha256(value: String): String =
        "sha256:" +
            MessageDigest.getInstance("SHA-256").digest(value.toByteArray()).joinToString("") { "%02x".format(java.util.Locale.ROOT, it) }

    private fun projection(
        objectMapper: ObjectMapper,
        name: String,
    ): String {
        val text = requireNotNull(javaClass.getResource("/s8-fake-e2e/$name")).readText().trim()
        objectMapper.readTree(text)
        return text
    }
}
