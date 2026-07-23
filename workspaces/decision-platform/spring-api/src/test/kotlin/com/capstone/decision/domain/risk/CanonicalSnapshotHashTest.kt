package com.capstone.decision.domain.risk

import com.capstone.decision.domain.principle.PrincipleMode
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNotEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import tools.jackson.databind.ObjectMapper
import java.math.BigDecimal
import java.time.Instant
import java.util.concurrent.Callable
import java.util.concurrent.Executors

class CanonicalSnapshotHashTest {
    private val hashes = SnapshotHashService()

    @Test
    fun `semantic hash ignores evaluation ids retrieval time and map insertion order`() {
        val left =
            snapshot(
                evaluationId = "eval_left",
                retrievedAt = Instant.parse("2030-01-02T03:04:06Z"),
                metrics =
                    linkedMapOf(
                        MetricKey.ASSET_WEIGHT to available("0.1500", "a"),
                        MetricKey.DAILY_ORDER_COUNT to availableWhole(3, "b"),
                    ),
            )
        val right =
            snapshot(
                evaluationId = "eval_right",
                retrievedAt = Instant.parse("2030-01-02T03:05:06Z"),
                metrics =
                    linkedMapOf(
                        MetricKey.DAILY_ORDER_COUNT to availableWhole(3, "b"),
                        MetricKey.ASSET_WEIGHT to available("0.15", "a"),
                    ),
            )

        assertEquals(hashes.semanticInputHash(left), hashes.semanticInputHash(right))
        assertNotEquals(hashes.snapshotArtifactHash(left), hashes.snapshotArtifactHash(right))
    }

    @Test
    fun `artifact hash covers every snapshot identity order and metric field`() {
        val base =
            MetricSnapshot.fixture(
                evaluationId = "eval_base",
                evaluationAsOf = OBSERVED_AT,
                retrievedAt = OBSERVED_AT.plusSeconds(1),
                metrics = mapOf(MetricKey.ASSET_WEIGHT to available("0.1500", "a")),
                provenanceRefs = listOf(ref("a")),
                requestedOptionalComponents = listOf("LIGHTGBM"),
                observedOptionalComponentEvidence =
                    listOf(
                        OptionalComponentEvidence(
                            componentId = "LIGHTGBM",
                            available = true,
                            reasonCode = null,
                            evidenceVersion = "model-v1",
                            completeness = "COMPLETE",
                            sourceRefs = listOf(ref("a")),
                        ),
                    ),
                disclosureEvidence =
                    DisclosureEvidenceIdentity(
                        completeness = "COMPLETE",
                        mappingVersion = "mapping-v1",
                        sourceRefs = listOf(ref("a")),
                    ),
            )
        val baseMetric = base.metric(MetricKey.ASSET_WEIGHT) as MetricCell.Available
        val baseOptional = base.observedOptionalComponentEvidence.single()
        val baseDisclosure = requireNotNull(base.disclosureEvidence)
        val mutations =
            listOf(
                base.copy(snapshotSchemaVersion = "s2.2-metric-snapshot-v2"),
                base.copy(evaluationId = "eval_other"),
                base.copy(evaluationAsOf = OBSERVED_AT.plusNanos(1)),
                base.copy(retrievedAt = OBSERVED_AT.plusSeconds(2)),
                base.copy(actorUserId = "usr_other"),
                base.copy(principle = base.principle.copy(principleId = "prc_other")),
                base.copy(principle = base.principle.copy(principleVersionId = "pvr_other")),
                base.copy(principle = base.principle.copy(version = 2)),
                base.copy(principle = base.principle.copy(mode = PrincipleMode.STRICT)),
                base.copy(principle = base.principle.copy(rulesHash = ref("b"))),
                base.copy(systemRuleCatalogVersion = 2),
                base.copy(readinessPolicyVersion = "s2-2-readiness-v2"),
                base.copy(portfolio = base.portfolio.copy(source = PortfolioSource.KIS_MOCK)),
                base.copy(portfolio = base.portfolio.copy(revision = "paper-revision-2")),
                base.copy(portfolio = base.portfolio.copy(ownerScopeHash = ref("c"))),
                base.copy(portfolio = base.portfolio.copy(positionCount = 1)),
                base.copy(orderIntent = base.orderIntent.copy(symbol = "000660")),
                base.copy(orderIntent = base.orderIntent.copy(side = "SELL")),
                base.copy(
                    orderIntent =
                        base.orderIntent.copy(
                            orderType = "LIMIT",
                            limitPrice = BigDecimal("9999.00"),
                        ),
                ),
                base.copy(orderIntent = base.orderIntent.copy(quantity = 2)),
                base.copy(
                    metrics =
                        mapOf(
                            MetricKey.ASSET_WEIGHT to
                                baseMetric.copy(
                                    value = MetricValue.Decimal(BigDecimal("0.16"), 4, MetricUnit.RATIO),
                                ),
                        ),
                ),
                base.copy(
                    metrics =
                        mapOf(
                            MetricKey.ASSET_WEIGHT to
                                baseMetric.copy(
                                    value = MetricValue.Decimal(BigDecimal("0.15"), 3, MetricUnit.RATIO),
                                ),
                        ),
                ),
                base.copy(
                    metrics =
                        mapOf(
                            MetricKey.ASSET_WEIGHT to
                                baseMetric.copy(observedAt = baseMetric.observedAt.minusNanos(1)),
                        ),
                ),
                base.copy(
                    metrics =
                        mapOf(
                            MetricKey.ASSET_WEIGHT to
                                baseMetric.copy(retrievedAt = baseMetric.retrievedAt.plusNanos(1)),
                        ),
                ),
                base.copy(
                    metrics =
                        mapOf(
                            MetricKey.ASSET_WEIGHT to
                                baseMetric.copy(freshUntil = baseMetric.freshUntil.plusNanos(1)),
                        ),
                ),
                base.copy(
                    metrics =
                        mapOf(
                            MetricKey.ASSET_WEIGHT to
                                baseMetric.copy(source = MetricSource.RISK_SNAPSHOT),
                        ),
                ),
                base.copy(
                    metrics =
                        mapOf(
                            MetricKey.ASSET_WEIGHT to
                                baseMetric.copy(sourceRef = ref("d")),
                        ),
                ),
                base.copy(
                    metrics =
                        mapOf(
                            MetricKey.ASSET_WEIGHT to
                                baseMetric.copy(sourceVersion = "fixture-v2"),
                        ),
                ),
                base.copy(
                    metrics =
                        mapOf(
                            MetricKey.ASSET_WEIGHT to
                                MetricCell.Missing(MetricIssueCode.SOURCE_MISSING),
                        ),
                ),
                base.copy(provenanceRefs = listOf(ref("e"))),
                base.copy(requestedOptionalComponents = listOf("BSM")),
                base.copy(
                    observedOptionalComponentEvidence =
                        listOf(baseOptional.copy(componentId = "BSM")),
                ),
                base.copy(
                    observedOptionalComponentEvidence =
                        listOf(baseOptional.copy(available = false, reasonCode = "SOURCE_MISSING")),
                ),
                base.copy(
                    observedOptionalComponentEvidence =
                        listOf(baseOptional.copy(evidenceVersion = "model-v2")),
                ),
                base.copy(
                    observedOptionalComponentEvidence =
                        listOf(baseOptional.copy(completeness = "PARTIAL")),
                ),
                base.copy(
                    observedOptionalComponentEvidence =
                        listOf(baseOptional.copy(sourceRefs = listOf(ref("b")))),
                ),
                base.copy(disclosureEvidence = baseDisclosure.copy(completeness = "EMPTY")),
                base.copy(disclosureEvidence = baseDisclosure.copy(mappingVersion = "mapping-v2")),
                base.copy(disclosureEvidence = baseDisclosure.copy(sourceRefs = listOf(ref("b")))),
            )
        val expected = hashes.snapshotArtifactHash(base)

        mutations.forEachIndexed { index, mutation ->
            assertNotEquals(expected, hashes.snapshotArtifactHash(mutation), "mutation[$index] was not artifact-hashed")
        }
    }

    @Test
    fun `semantic hash covers full order intent freshness and source identity`() {
        val base =
            MetricSnapshot.fixture(
                evaluationAsOf = OBSERVED_AT,
                metrics = mapOf(MetricKey.ASSET_WEIGHT to available("0.15", "a")),
            )
        val available = base.metric(MetricKey.ASSET_WEIGHT) as MetricCell.Available
        val limitOrder =
            base.copy(
                orderIntent =
                    base.orderIntent.copy(
                        orderType = "LIMIT",
                        limitPrice = BigDecimal("10000.00"),
                    ),
            )
        val changedLimit =
            limitOrder.copy(
                orderIntent = limitOrder.orderIntent.copy(limitPrice = BigDecimal("10001")),
            )
        val changedFreshUntil =
            base.copy(
                metrics =
                    mapOf(
                        MetricKey.ASSET_WEIGHT to available.copy(freshUntil = available.freshUntil.plusNanos(1)),
                    ),
            )
        val changedSource =
            base.copy(
                metrics =
                    mapOf(
                        MetricKey.ASSET_WEIGHT to available.copy(sourceVersion = "fixture-v2"),
                    ),
            )

        assertNotEquals(hashes.semanticInputHash(base), hashes.semanticInputHash(limitOrder))
        assertNotEquals(hashes.semanticInputHash(limitOrder), hashes.semanticInputHash(changedLimit))
        assertNotEquals(hashes.semanticInputHash(base), hashes.semanticInputHash(changedFreshUntil))
        assertNotEquals(hashes.semanticInputHash(base), hashes.semanticInputHash(changedSource))
    }

    @Test
    fun `snapshot retrieval time is non-semantic while per-metric freshness remains semantic`() {
        val base =
            MetricSnapshot.fixture(
                evaluationAsOf = OBSERVED_AT,
                retrievedAt = OBSERVED_AT.plusSeconds(1),
                metrics = mapOf(MetricKey.ASSET_WEIGHT to available("0.15", "a")),
            )
        val later = base.copy(retrievedAt = OBSERVED_AT.plusSeconds(10))

        assertEquals(hashes.semanticInputHash(base), hashes.semanticInputHash(later))
        assertNotEquals(hashes.snapshotArtifactHash(base), hashes.snapshotArtifactHash(later))
    }

    @Test
    fun `full artifact contains every metric key and canonical stable arrays`() {
        val left =
            MetricSnapshot.fixture(
                metrics =
                    linkedMapOf(
                        MetricKey.DAILY_ORDER_COUNT to availableWhole(3, "b"),
                        MetricKey.ASSET_WEIGHT to available("0.15", "a"),
                    ),
                provenanceRefs = listOf(ref("c"), ref("a")),
            )
        val right =
            left.copy(
                metrics =
                    linkedMapOf(
                        MetricKey.ASSET_WEIGHT to left.metric(MetricKey.ASSET_WEIGHT),
                        MetricKey.DAILY_ORDER_COUNT to left.metric(MetricKey.DAILY_ORDER_COUNT),
                    ),
                provenanceRefs = left.provenanceRefs.reversed(),
            )
        val canonical = hashes.snapshotArtifactCanonicalJson(left)

        MetricKey.entries.forEach { key ->
            assertTrue(canonical.contains(""""metric":"${key.wireName}""""))
        }
        assertEquals(canonical, hashes.snapshotArtifactCanonicalJson(right))
    }

    @Test
    fun `artifact preserves every typed unavailable metric state and reason`() {
        val snapshot =
            MetricSnapshot.fixture(
                metrics =
                    mapOf(
                        MetricKey.ASSET_WEIGHT to MetricCell.Missing(MetricIssueCode.SOURCE_MISSING),
                        MetricKey.DAILY_LOSS_RATE to
                            MetricCell.Stale(
                                observedAt = OBSERVED_AT.minusSeconds(301),
                                freshUntil = OBSERVED_AT.minusNanos(1),
                                reason = MetricIssueCode.SOURCE_STALE,
                            ),
                        MetricKey.MDD to MetricCell.Error(MetricIssueCode.SOURCE_ERROR),
                        MetricKey.NEGATIVE_NEWS_SCORE to
                            MetricCell.Incomplete(MetricIssueCode.SOURCE_INCOMPLETE),
                        MetricKey.HMM_RISK_OFF_PROBABILITY to
                            MetricCell.Abstained(MetricIssueCode.MODEL_ABSTAINED),
                        MetricKey.ETF_ETN_RISK_SCORE to
                            MetricCell.NotApplicable(MetricIssueCode.NOT_APPLICABLE),
                    ),
            )
        val canonical = hashes.snapshotArtifactCanonicalJson(snapshot)

        listOf("MISSING", "STALE", "ERROR", "INCOMPLETE", "ABSTAINED", "NOT_APPLICABLE").forEach {
            assertTrue(canonical.contains(""""availability":"$it""""))
        }
        MetricIssueCode.entries
            .filter {
                it in
                    setOf(
                        MetricIssueCode.SOURCE_MISSING,
                        MetricIssueCode.SOURCE_STALE,
                        MetricIssueCode.SOURCE_ERROR,
                        MetricIssueCode.SOURCE_INCOMPLETE,
                        MetricIssueCode.MODEL_ABSTAINED,
                        MetricIssueCode.NOT_APPLICABLE,
                    )
            }.forEach { assertTrue(canonical.contains(""""reason":"${it.name}"""")) }
    }

    @Test
    fun `canonical decimal uses plain form removes trailing zeros and normalizes negative zero`() {
        assertEquals("0", CanonicalJson.decimal(BigDecimal("-0.0000")))
        assertEquals("1.23", CanonicalJson.decimal(BigDecimal("1.2300")))
        assertEquals("1000", CanonicalJson.decimal(BigDecimal("1E+3")))
    }

    @Test
    fun `canonical object keys and explicitly sorted arrays produce exact stable vector`() {
        val canonical =
            CanonicalJson.encode(
                mapOf(
                    "z" to BigDecimal("-0.00"),
                    "a" to listOf("b", "a").sorted(),
                    "n" to BigDecimal("1.2300"),
                ),
            )

        assertEquals("""{"a":["a","b"],"n":"1.23","z":"0"}""", canonical)
        assertEquals("49fd0f441a7bb74dc7f649eb24f74ec98c048e6cbc63b49cac991b1488c346a0", CanonicalJson.sha256(canonical))
    }

    @Test
    fun `artifact and semantic canonical bytes cannot be mutated through their public boundary`() {
        val artifact =
            MetricSnapshotArtifactV1.from(
                snapshot(
                    evaluationId = "eval_immutable_bytes",
                    retrievedAt = OBSERVED_AT.plusSeconds(1),
                    metrics = mapOf(MetricKey.ASSET_WEIGHT to available("0.15", "f")),
                ),
            )
        val artifactBytes = artifact.canonicalBytes
        artifactBytes[0] = (artifactBytes[0].toInt() xor 1).toByte()

        assertEquals(artifact.sha256, CanonicalJson.sha256(artifact.canonicalBytes))

        val semantic = artifact.semanticInput()
        val semanticBytes = semantic.canonicalBytes
        semanticBytes[0] = (semanticBytes[0].toInt() xor 1).toByte()

        assertEquals(semantic.sha256, CanonicalJson.sha256(semantic.canonicalBytes))
    }

    @Test
    fun `same semantic snapshot hashes identically one hundred times`() {
        val snapshot =
            snapshot(
                evaluationId = "eval_repeatable",
                retrievedAt = OBSERVED_AT.plusSeconds(1),
                metrics = mapOf(MetricKey.ASSET_WEIGHT to available("0.1500", "d")),
            )
        val expected = hashes.semanticInputHash(snapshot)

        repeat(100) {
            assertEquals(expected, hashes.semanticInputHash(snapshot))
        }

        Executors.newFixedThreadPool(8).use { executor ->
            val results =
                executor.invokeAll(
                    (0 until 100).map {
                        Callable { hashes.semanticInputCanonicalJson(snapshot) to hashes.semanticInputHash(snapshot) }
                    },
                )
            results.forEach { future ->
                assertEquals(hashes.semanticInputCanonicalJson(snapshot) to expected, future.get())
            }
        }
    }

    @Test
    fun `optional evidence identity fields obey the approved text bounds`() {
        val overlong = "x".repeat(EvaluationBounds.MAX_ID_OR_CODE_CHARS + 1)

        assertThrows<IllegalArgumentException> {
            OptionalComponentEvidence(
                componentId = "LIGHTGBM",
                available = false,
                reasonCode = overlong,
            )
        }
        assertThrows<IllegalArgumentException> {
            OptionalComponentEvidence(
                componentId = "LIGHTGBM",
                available = true,
                reasonCode = null,
                evidenceVersion = overlong,
            )
        }
        assertThrows<IllegalArgumentException> {
            MetricSnapshot.fixture().copy(readinessPolicyVersion = overlong)
        }
    }

    @Test
    fun `jvm canonical bytes and hashes match the generated s2-2 v1 vector exactly`() {
        val sourceRef = "1".repeat(64)
        val observedAt = Instant.parse("2026-07-23T00:00:00Z")
        val retrievedAt = Instant.parse("2026-07-23T00:00:01Z")
        val snapshot =
            MetricSnapshot(
                snapshotSchemaVersion = "s2.2-metric-snapshot-v1",
                evaluationId = "evl_0123456789abcdef",
                evaluationAsOf = observedAt,
                retrievedAt = retrievedAt,
                actorUserId = "usr_hash_fixture",
                principle =
                    PrincipleSnapshotIdentity(
                        principleId = "prc_0123456789abcdef0123456789abcdef",
                        principleVersionId = "pvr_0123456789abcdef0123456789abcdef",
                        version = 3,
                        mode = PrincipleMode.GUIDE,
                        rulesHash = "2".repeat(64),
                    ),
                systemRuleCatalogVersion = 1,
                readinessPolicyVersion = "s2-2-readiness-v1",
                portfolio =
                    PortfolioSnapshotIdentity(
                        source = PortfolioSource.INTERNAL_PAPER,
                        revision = "portfolio-revision-7",
                        ownerScopeHash = "3".repeat(64),
                        positionCount = 1,
                    ),
                orderIntent =
                    OrderIntentSnapshot(
                        symbol = "005930",
                        side = "BUY",
                        orderType = "LIMIT",
                        quantity = 10,
                        limitPrice = BigDecimal("50000.00"),
                    ),
                metrics =
                    mapOf(
                        MetricKey.ORDER_AMOUNT_KRW to
                            exactAvailable(
                                MetricValue.Whole(500_000, MetricUnit.KRW),
                                observedAt,
                                retrievedAt,
                                sourceRef,
                            ),
                        MetricKey.ASSET_WEIGHT to
                            exactAvailable(
                                MetricValue.Decimal(BigDecimal("0.2000"), 4, MetricUnit.RATIO),
                                observedAt,
                                retrievedAt,
                                sourceRef,
                            ),
                    ),
                provenanceRefs = listOf(sourceRef),
                requestedOptionalComponents = listOf("DISCLOSURE"),
                observedOptionalComponentEvidence =
                    listOf(
                        OptionalComponentEvidence(
                            componentId = "DISCLOSURE",
                            available = true,
                            reasonCode = null,
                            evidenceVersion = "s1.2-v1",
                            completeness = "COMPLETE",
                            sourceRefs = listOf(sourceRef),
                        ),
                    ),
                disclosureEvidence =
                    DisclosureEvidenceIdentity(
                        completeness = "COMPLETE",
                        mappingVersion = "s1.2-v1",
                        sourceRefs = listOf(sourceRef),
                    ),
            )
        val vector =
            requireNotNull(javaClass.getResourceAsStream("/contracts/s2-2-hash-vector.valid.json")).use { input ->
                ObjectMapper().readTree(input)
            }

        assertEquals(vector.path("semanticInputCanonicalJson").stringValue(), hashes.semanticInputCanonicalJson(snapshot))
        assertEquals(vector.path("snapshotArtifactCanonicalJson").stringValue(), hashes.snapshotArtifactCanonicalJson(snapshot))
        assertEquals(vector.path("semanticInputHash").stringValue(), hashes.semanticInputHash(snapshot))
        assertEquals(vector.path("snapshotArtifactHash").stringValue(), hashes.snapshotArtifactHash(snapshot))
    }

    private fun snapshot(
        evaluationId: String,
        retrievedAt: Instant,
        metrics: Map<MetricKey, MetricCell<MetricValue>>,
    ): MetricSnapshot =
        MetricSnapshot.fixture(
            evaluationId = evaluationId,
            retrievedAt = retrievedAt,
            metrics = metrics,
            provenanceRefs = listOf(ref("c"), ref("a")).reversed(),
        )

    private fun available(
        value: String,
        seed: String,
    ): MetricCell.Available<MetricValue> =
        MetricCell.Available(
            value = MetricValue.Decimal(BigDecimal(value), 4, MetricUnit.RATIO),
            observedAt = OBSERVED_AT,
            retrievedAt = OBSERVED_AT.plusSeconds(if (seed == "a") 1 else 2),
            freshUntil = OBSERVED_AT.plusSeconds(300),
            source = MetricSource.INTERNAL,
            sourceRef = ref(seed),
            sourceVersion = "fixture-v1",
        )

    private fun availableWhole(
        value: Long,
        seed: String,
    ): MetricCell.Available<MetricValue> =
        MetricCell.Available(
            value = MetricValue.Whole(value, MetricUnit.COUNT),
            observedAt = OBSERVED_AT,
            retrievedAt = OBSERVED_AT.plusSeconds(1),
            freshUntil = OBSERVED_AT.plusSeconds(300),
            source = MetricSource.INTERNAL,
            sourceRef = ref(seed),
            sourceVersion = "fixture-v1",
        )

    private fun exactAvailable(
        value: MetricValue,
        observedAt: Instant,
        retrievedAt: Instant,
        sourceRef: String,
    ): MetricCell.Available<MetricValue> =
        MetricCell.Available(
            value = value,
            observedAt = observedAt,
            retrievedAt = retrievedAt,
            freshUntil = observedAt.plusSeconds(300),
            source = MetricSource.INTERNAL_PAPER,
            sourceRef = sourceRef,
            sourceVersion = "paper-v1",
        )

    private fun ref(seed: String): String = seed.repeat(64)

    companion object {
        private val OBSERVED_AT = Instant.parse("2030-01-02T03:04:05Z")
    }
}
