package com.capstone.decision.api.financialengineering

import com.capstone.decision.application.financialengineering.FinancialEngineeringNumericPort
import com.capstone.decision.application.financialengineering.FinancialEngineeringService
import com.capstone.decision.infrastructure.grpc.FinancialEngineeringGrpcProperties
import com.capstone.decision.infrastructure.grpc.GrpcFinancialEngineeringAdapter
import org.hamcrest.Matchers.closeTo
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.support.StaticListableBeanFactory
import org.springframework.http.MediaType
import org.springframework.test.web.servlet.post
import org.springframework.test.web.servlet.setup.MockMvcBuilders
import tools.jackson.databind.json.JsonMapper
import java.io.IOException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.nio.file.Files
import java.nio.file.Path
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * REST JSON projection, Kotlin gRPC adapter, and the real Python numeric kernel are joined here.
 * The child process is loopback-only and inherits no provider credentials.
 */
class FinancialEngineeringSpringPythonE2eTest {
    @Test
    fun `REST endpoints preserve the public erratum through the Python process boundary`() {
        withPythonServer { properties ->
            val adapter = GrpcFinancialEngineeringAdapter(properties)
            try {
                val service =
                    FinancialEngineeringService(
                        JsonMapper.builder().build(),
                        StaticListableBeanFactory(mapOf("numericPort" to adapter))
                            .getBeanProvider(FinancialEngineeringNumericPort::class.java),
                    )
                val mockMvc = MockMvcBuilders.standaloneSetup(FinancialEngineeringController(service)).build()

                mockMvc.post("/api/v1/financial-engineering/options/black-scholes") {
                    contentType = MediaType.APPLICATION_JSON
                    content = request("CALL", "volatility", "0.28")
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.data.discountedValue", closeTo(2917.937245391, 1e-9))
                    jsonPath("$.data.measure") { value("Q_DISCOUNTED_VALUE") }
                    jsonPath("$.data.provenance.termsId") { value(CALL_TERMS) }
                }

                val greeksResponse = mockMvc.post("/api/v1/financial-engineering/options/greeks") {
                    contentType = MediaType.APPLICATION_JSON
                    content = request("PUT", "volatility", "0.28")
                }.andExpect {
                    status { isOk() }
                }.andReturn().response.contentAsString
                val greeks = JsonMapper.builder().build().readTree(greeksResponse).path("data")
                assertEquals(-0.570897306492, greeks.path("valuationDelta").doubleValue(), 5e-13)
                assertEquals(-1.0, greeks.path("conservativeRiskDelta").doubleValue(), 0.0)
                assertEquals(0.000038828202272, greeks.path("gamma").doubleValue(), 5e-16)
                assertEquals(140.899780404, greeks.path("vegaPerVolPoint").doubleValue(), 5e-10)
                assertEquals(-6810.08298, greeks.path("calendarThetaPerYear").doubleValue(), 5e-5)
                assertEquals(-18.6577616, greeks.path("calendarThetaPerDay").doubleValue(), 5e-7)
                assertEquals(-116.5117803, greeks.path("rhoPerRatePoint").doubleValue(), 5e-7)

                mockMvc.post("/api/v1/financial-engineering/options/implied-volatility") {
                    contentType = MediaType.APPLICATION_JSON
                    content = request("CALL", "marketPrice", "2917.937245391")
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.data.impliedVolatility", closeTo(0.28, 5e-9))
                    jsonPath("$.data.solver") { value("BOUNDED_BISECTION_0.0001_5.0") }
                }
            } finally {
                adapter.close()
            }
        }
    }

    private fun request(
        right: String,
        numericName: String,
        numericValue: String,
    ): String {
        val termsId = if (right == "CALL") CALL_TERMS else PUT_TERMS
        return """
            {
              "contractId":"$termsId",
              "valuationAt":"2026-06-11T09:20:00+09:00",
              "spot":72000.0,
              "$numericName":$numericValue,
              "riskFreeRate":0.032,
              "dividendYield":0.01
            }
        """.trimIndent()
    }

    private fun withPythonServer(block: (FinancialEngineeringGrpcProperties) -> Unit) {
        val port = reserveLoopbackPort()
        val sharedSecret = "s6-local-test-${UUID.randomUUID().toString().replace("-", "")}"
        val properties =
            FinancialEngineeringGrpcProperties(
                enabled = true,
                target = "127.0.0.1:$port",
                sharedSecret = sharedSecret,
            )
        properties.validateEnabled()
        val process = startPythonServer(port, sharedSecret)
        try {
            awaitLoopbackReady(process, port)
            block(properties)
        } finally {
            terminateProcess(process)
        }
    }

    private fun reserveLoopbackPort(): Int =
        ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")).use { it.localPort }

    private fun startPythonServer(
        port: Int,
        sharedSecret: String,
    ): Process {
        val pythonServices = repositoryRoot().resolve(PYTHON_SERVICES_RELATIVE_PATH)
        check(Files.isRegularFile(pythonServices.resolve("pyproject.toml"))) {
            "S6 financial-engineering Python project is unavailable."
        }
        val builder =
            ProcessBuilder(
                "uv",
                "run",
                "--frozen",
                "--no-sync",
                "python",
                "-m",
                "app.financial_engineering",
            ).directory(pythonServices.toFile())
                .redirectOutput(ProcessBuilder.Redirect.DISCARD)
                .redirectError(ProcessBuilder.Redirect.DISCARD)
        builder.environment().apply {
            put("PYTHONDONTWRITEBYTECODE", "1")
            put("UV_OFFLINE", "1")
            put("FINANCIAL_ENGINEERING_GRPC_BIND_ADDRESS", "127.0.0.1:$port")
            put("FINANCIAL_ENGINEERING_GRPC_SHARED_SECRET", sharedSecret)
            PROVIDER_CREDENTIAL_KEYS.forEach(::remove)
        }
        return try {
            builder.start()
        } catch (exception: IOException) {
            throw AssertionError("S6 integration test requires the frozen uv Python runtime.", exception)
        }
    }

    private fun awaitLoopbackReady(
        process: Process,
        port: Int,
    ) {
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10)
        while (System.nanoTime() < deadline) {
            check(process.isAlive) { "S6 Python process exited before loopback readiness." }
            try {
                Socket().use { it.connect(InetSocketAddress("127.0.0.1", port), 250) }
                return
            } catch (_: IOException) {
                try {
                    Thread.sleep(50)
                } catch (exception: InterruptedException) {
                    Thread.currentThread().interrupt()
                    throw AssertionError("Interrupted while waiting for the S6 Python process.", exception)
                }
            }
        }
        throw AssertionError("S6 Python process did not bind loopback in time.")
    }

    private fun terminateProcess(process: Process) {
        val descendants = process.toHandle().descendants().use { it.toList() }
        descendants.filter { it.isAlive }.forEach { it.destroy() }
        if (process.isAlive) process.destroy()
        if (!process.waitFor(5, TimeUnit.SECONDS)) {
            descendants.filter { it.isAlive }.forEach { it.destroyForcibly() }
            if (process.isAlive) process.destroyForcibly()
            check(process.waitFor(5, TimeUnit.SECONDS)) { "S6 Python process did not terminate." }
        }
    }

    private fun repositoryRoot(): Path {
        var candidate = Path.of(System.getProperty("user.dir")).toAbsolutePath().normalize()
        while (true) {
            if (Files.isRegularFile(candidate.resolve(PYTHON_SERVICES_RELATIVE_PATH).resolve("pyproject.toml"))) {
                return candidate
            }
            candidate = candidate.parent ?: break
        }
        throw AssertionError("Could not locate the repository root from the Gradle working directory.")
    }

    private companion object {
        const val CALL_TERMS = "KOSPI200_OPTION_FIXTURE_202609_CALL_75000"
        const val PUT_TERMS = "KOSPI200_OPTION_FIXTURE_202609_PUT_75000"
        val PYTHON_SERVICES_RELATIVE_PATH: Path = Path.of("workspaces", "decision-platform", "python-services")
        val PROVIDER_CREDENTIAL_KEYS =
            listOf(
                "GEMINI_API_KEY",
                "GOOGLE_API_KEY",
                "KIS_APP_KEY",
                "KIS_APP_SECRET",
                "OPENAI_API_KEY",
                "VOYAGE_API_KEY",
            )
    }
}
