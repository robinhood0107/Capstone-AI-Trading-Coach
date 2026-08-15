package com.capstone.decision

import com.capstone.decision.application.signal.SignalV2CompositeAbstain
import com.capstone.decision.application.signal.SignalV2CompositeAvailable
import com.capstone.decision.application.signal.SignalV2Contract
import com.capstone.decision.application.signal.SignalV2PredictiveAvailable
import com.capstone.decision.application.signal.SignalV2RegimeAbstain
import com.capstone.decision.application.signal.SignalV1Contract
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import tools.jackson.databind.json.JsonMapper
import java.nio.file.Files
import java.nio.file.Path

class SignalV2ContractParityTest {
    private val repositoryRoot: Path =
        Path
            .of("../../..")
            .toAbsolutePath()
            .normalize()

    @Test
    fun `Spring parser accepts the Python generated AVAILABLE and ABSTAIN unions`() {
        val available = validateFixture("signal-v2.available.valid.json")
        val abstain = validateFixture("signal-v2.abstain.valid.json")

        assertThat(available.composite).isInstanceOf(SignalV2CompositeAvailable::class.java)
        assertThat((available.composite as SignalV2CompositeAvailable).signal.name).isEqualTo("HOLD")
        assertThat(available.components.lightgbm).isInstanceOf(SignalV2PredictiveAvailable::class.java)
        assertThat(abstain.composite).isInstanceOf(SignalV2CompositeAbstain::class.java)
        assertThat(abstain.components.hmmRegime).isInstanceOf(SignalV2RegimeAbstain::class.java)
    }

    @Test
    fun `Spring parser rejects every Python generated Signal v2 negative fixture`() {
        val invalidDirectory = repositoryRoot.resolve("contracts/examples/invalid")
        val invalidFiles =
            Files
                .list(invalidDirectory)
                .use { paths ->
                    paths
                        .filter {
                            it.fileName
                                .toString()
                                .matches(Regex("""signal-v2\..+\.invalid\.json"""))
                        }.sorted()
                        .toList()
                }

        assertThat(invalidFiles.size).isGreaterThanOrEqualTo(4)
        invalidFiles.forEach { path ->
            assertThatThrownBy { SignalV2Contract.validate(Files.readAllBytes(path)) }
                .isInstanceOf(IllegalArgumentException::class.java)
                .hasMessageContaining("Signal v2")
        }
    }

    @Test
    fun `Spring parsers reject each generated v1 and v2 adjacent-authority fixture`() {
        val invalidDirectory = repositoryRoot.resolve("contracts/examples/invalid")
        val fields =
            listOf(
                "cross-market-score",
                "cross-market-mode",
                "cross-market-freshness",
                "cross-market-exposure",
                "analyst",
                "news",
                "cause",
                "rag",
                "llm",
                "risk-decision",
                "order-authority",
            )
        fields.forEach { field ->
            val v1 = invalidDirectory.resolve("signal.unknown-$field.invalid.json")
            val v2 = invalidDirectory.resolve("signal-v2.unknown-$field.invalid.json")
            assertThatThrownBy { SignalV1Contract.validate(Files.readAllBytes(v1)) }
                .isInstanceOf(IllegalArgumentException::class.java)
                .hasMessageContaining("Signal v1")
            assertThatThrownBy { SignalV2Contract.validate(Files.readAllBytes(v2)) }
                .isInstanceOf(IllegalArgumentException::class.java)
                .hasMessageContaining("Signal v2")
        }
    }

    @Test
    fun `runtime parser accepts all-abstain partial-abstain and available HOLD`() {
        val allAbstain = SignalV2Contract.validateRuntime(fixtureBytes("signal-v2-runtime-v1.all-abstain.valid.json"))
        val partial = SignalV2Contract.validateRuntime(fixtureBytes("signal-v2-runtime-v1.partial-abstain.valid.json"))
        val available = SignalV2Contract.validateRuntime(fixtureBytes("signal-v2-runtime-v1.available-hold.valid.json"))

        assertThat(allAbstain.asOf).isNull()
        assertThat(allAbstain.modelReportId).isNull()
        assertThat(allAbstain.composite).isInstanceOf(SignalV2CompositeAbstain::class.java)
        assertThat(partial.asOf).isNotNull()
        assertThat(partial.composite).isInstanceOf(SignalV2CompositeAbstain::class.java)
        assertThat((available.composite as SignalV2CompositeAvailable).signal.name).isEqualTo("HOLD")
    }

    @Test
    fun `stale failure drift and missing evidence remain typed ABSTAIN without fabricated values`() {
        val mapper = JsonMapper.builder().build()
        val base = mapper.readTree(fixtureBytes("signal-v2.available.valid.json"))
        listOf(
            "STALE_EVIDENCE",
            "PRODUCER_FAILED",
            "ARTIFACT_DRIFT",
            "MISSING_EVIDENCE",
        ).forEach { reason ->
            val root = base.deepCopy()
            val components = root.path("components")
            val lightgbm = mapper.createObjectNode()
            lightgbm.put("status", "ABSTAIN")
            lightgbm.put("producer", "LIGHTGBM")
            lightgbm.put("sourceWorkspace", "decision-platform")
            lightgbm.put("reason", reason)
            (components as tools.jackson.databind.node.ObjectNode).set("lightgbm", lightgbm)
            val composite = mapper.createObjectNode()
            composite.put("status", "ABSTAIN")
            composite.put("reason", "REQUIRED_COMPONENT_UNAVAILABLE")
            (root as tools.jackson.databind.node.ObjectNode).set("composite", composite)

            val parsed = SignalV2Contract.validate(mapper.writeValueAsBytes(root))

            assertThat(parsed.composite).isInstanceOf(SignalV2CompositeAbstain::class.java)
            assertThat(parsed.components.lightgbm)
                .extracting("reason")
                .isEqualTo(reason)
        }
    }

    @Test
    fun `Signal v2 contract lock does not publish an active endpoint`() {
        val openApi =
            JsonMapper.builder().build().readTree(
                Files.readAllBytes(repositoryRoot.resolve("contracts/openapi/openapi.json")),
            )

        assertThat(openApi.path("paths").has("/api/v2/signals/{symbol}")).isFalse()
    }

    private fun validateFixture(fileName: String) = SignalV2Contract.validate(fixtureBytes(fileName))

    private fun fixtureBytes(fileName: String) = Files.readAllBytes(fixturePath(fileName))

    private fun fixturePath(fileName: String) = repositoryRoot.resolve("contracts/examples").resolve(fileName)
}
