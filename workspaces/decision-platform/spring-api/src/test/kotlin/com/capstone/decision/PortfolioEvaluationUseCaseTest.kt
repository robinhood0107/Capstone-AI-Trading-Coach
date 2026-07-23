package com.capstone.decision

import com.capstone.decision.application.risk.MetricSnapshotAssembler
import com.capstone.decision.application.risk.OptionalEvaluationComponent
import com.capstone.decision.application.risk.PortfolioEvaluationCommand
import com.capstone.decision.application.risk.PortfolioEvaluationUseCase
import com.capstone.decision.application.risk.port.ActivePrincipleSnapshot
import com.capstone.decision.application.risk.port.BalancePort
import com.capstone.decision.application.risk.port.BalanceSnapshot
import com.capstone.decision.application.risk.port.DisclosureEventEvidence
import com.capstone.decision.application.risk.port.DisclosureRiskPort
import com.capstone.decision.application.risk.port.DisclosureRiskSnapshot
import com.capstone.decision.application.risk.port.EvaluationSourceRequest
import com.capstone.decision.application.risk.port.InstrumentCatalogPort
import com.capstone.decision.application.risk.port.InstrumentSnapshot
import com.capstone.decision.application.risk.port.MarginPort
import com.capstone.decision.application.risk.port.NewsEvidencePort
import com.capstone.decision.application.risk.port.OptionalComponentEvidence
import com.capstone.decision.application.risk.port.OrderMetricPort
import com.capstone.decision.application.risk.port.PortfolioContextPort
import com.capstone.decision.application.risk.port.PortfolioContextRef
import com.capstone.decision.application.risk.port.PortfolioContextResolution
import com.capstone.decision.application.risk.port.PortfolioPosition
import com.capstone.decision.application.risk.port.PricePort
import com.capstone.decision.application.risk.port.PrincipleSnapshotPort
import com.capstone.decision.application.risk.port.RiskMetricBundle
import com.capstone.decision.application.risk.port.RiskSnapshotPort
import com.capstone.decision.application.risk.port.SignalMetricBundle
import com.capstone.decision.application.risk.port.SignalPort
import com.capstone.decision.domain.principle.EvidenceRequirement
import com.capstone.decision.domain.principle.PrincipleId
import com.capstone.decision.domain.principle.PrincipleMode
import com.capstone.decision.domain.principle.PrincipleRule
import com.capstone.decision.domain.principle.PrincipleValidationException
import com.capstone.decision.domain.principle.PrincipleVersionId
import com.capstone.decision.domain.risk.EvaluationAction
import com.capstone.decision.domain.risk.MetricCell
import com.capstone.decision.domain.risk.MetricIssueCode
import com.capstone.decision.domain.risk.MetricKey
import com.capstone.decision.domain.risk.MetricSource
import com.capstone.decision.domain.risk.MetricUnit
import com.capstone.decision.domain.risk.MetricValue
import com.capstone.decision.domain.risk.OrderIntentSnapshot
import com.capstone.decision.domain.risk.PortfolioSource
import com.capstone.decision.infrastructure.risk.ClasspathSystemRuleCatalog
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import tools.jackson.databind.json.JsonMapper
import java.math.BigDecimal
import java.time.Instant

class PortfolioEvaluationUseCaseTest {
    @Test
    fun `invalid portfolio selector fails before every owner and source port`() {
        val harness = Harness()

        val error =
            assertThrows<PrincipleValidationException> {
                harness.useCase.evaluate(harness.command(portfolioSource = "AUTO"))
            }

        assertThat(error.violations).containsExactly(
            com.capstone.decision.domain.principle.PrincipleViolation(
                "/portfolioSource",
                "INVALID_ENUM",
            ),
        )
        assertThat(harness.principleCalls).isZero()
        assertThat(harness.contextCalls).isZero()
        assertThat(harness.sourceCalls()).isZero()
    }

    @Test
    fun `owner context unavailability returns business HOLD without source assembly`() {
        val harness = Harness(contextAvailable = false)

        val evaluation = harness.useCase.evaluate(harness.command())

        assertThat(evaluation.result.action).isEqualTo(EvaluationAction.HOLD)
        assertThat(evaluation.result.issues.map { it.ruleId }).containsExactly("data_freshness_guard")
        assertThat(evaluation.snapshot).isNotNull()
        assertThat(evaluation.semanticInputHash).matches("[0-9a-f]{64}")
        assertThat(evaluation.snapshotArtifactHash).matches("[0-9a-f]{64}")
        assertThat(harness.principleCalls).isEqualTo(1)
        assertThat(harness.contextCalls).isEqualTo(1)
        assertThat(harness.sourceCalls()).isZero()
    }

    @Test
    fun `KIS balance failure does not call PAPER fallback and each selected port is called at most once`() {
        val harness =
            Harness(
                kisBalanceCell = MetricCell.Error(MetricIssueCode.BROKERAGE_UNAVAILABLE),
            )

        val evaluation =
            harness.useCase.evaluate(
                harness.command(portfolioSource = PortfolioSource.KIS_MOCK.name),
            )

        assertThat(evaluation.result.action).isEqualTo(EvaluationAction.HOLD)
        assertThat(harness.kisBalanceCalls).isEqualTo(1)
        assertThat(harness.paperBalanceCalls).isZero()
        assertThat(harness.allSourceCallCounts()).allMatch { it <= 1 }
    }

    @Test
    fun `explicit PAPER mode never probes KIS and pins one immutable principle snapshot`() {
        val harness = Harness()

        val evaluation = harness.useCase.evaluate(harness.command())
        val snapshot = requireNotNull(evaluation.snapshot)

        assertThat(evaluation.result.action).isEqualTo(EvaluationAction.ALLOW)
        assertThat(evaluation.portfolioSource).isEqualTo(PortfolioSource.INTERNAL_PAPER)
        assertThat(evaluation.principle.principleVersionId.value)
            .isEqualTo("pvr_0123456789abcdef0123456789abcdef")
        assertThat(snapshot.principle.principleVersionId)
            .isEqualTo("pvr_0123456789abcdef0123456789abcdef")
        assertThat(
            (snapshot.metric(MetricKey.ASSET_WEIGHT) as MetricCell.Available).value,
        ).isInstanceOf(MetricValue.RatioFraction::class.java)
        assertThat(
            (
                (snapshot.metric(MetricKey.OWNER_POSITION_QUANTITY) as MetricCell.Available)
                    .value as MetricValue.Whole
            ).value,
        ).isEqualTo(10)
        assertThat(evaluation.semanticInputHash).matches("[0-9a-f]{64}")
        assertThat(evaluation.snapshotArtifactHash).matches("[0-9a-f]{64}")
        assertThat(harness.paperBalanceCalls).isEqualTo(1)
        assertThat(harness.kisBalanceCalls).isZero()
        assertThat(harness.allSourceCallCounts()).allMatch { it <= 1 }
    }

    @Test
    fun `missing optional disclosure abstains only that rule while required disclosure holds`() {
        val optional =
            Harness(
                disclosureRequirement = EvidenceRequirement.OPTIONAL,
                disclosureCell = MetricCell.Missing(MetricIssueCode.DISCLOSURE_UNAVAILABLE),
            )
        val required =
            Harness(
                disclosureRequirement = EvidenceRequirement.REQUIRED,
                disclosureCell = MetricCell.Missing(MetricIssueCode.DISCLOSURE_UNAVAILABLE),
            )

        val optionalResult = optional.useCase.evaluate(optional.command()).result
        val requiredResult = required.useCase.evaluate(required.command()).result

        assertThat(optionalResult.action).isEqualTo(EvaluationAction.WARN)
        assertThat(optionalResult.issues).noneMatch { it.ruleId == "disclosure_risk_guard" }
        assertThat(optionalResult.abstentions.map { it.ruleId }).contains("disclosure_risk_guard")
        assertThat(optionalResult.warnings.map { it.ruleId }).contains("disclosure_risk_guard")
        assertThat(requiredResult.action).isEqualTo(EvaluationAction.HOLD)
        assertThat(requiredResult.issues.map { it.ruleId }).contains("disclosure_risk_guard")
    }

    @Test
    fun `optional model components cannot create HOLD or BLOCK by themselves`() {
        val harness =
            Harness(
                signalBundle =
                    SignalMetricBundle(
                        hmmRiskOffProbability = MetricCell.Missing(MetricIssueCode.MODEL_ABSTAINED),
                        meanReversionAbsoluteZScore =
                            available(decimal("0.5", MetricUnit.ABS_Z_SCORE), MetricSource.SIGNAL, "9"),
                        optionalComponents =
                            listOf(
                                OptionalComponentEvidence(
                                    componentId = OptionalEvaluationComponent.LIGHTGBM.name,
                                    available = false,
                                    reasonCode = "INTERNAL_MODEL_UNAVAILABLE",
                                ),
                            ),
                    ),
            )

        val result =
            harness.useCase
                .evaluate(
                    harness.command(
                        optionalComponents =
                            setOf(
                                OptionalEvaluationComponent.HMM,
                                OptionalEvaluationComponent.LIGHTGBM,
                                OptionalEvaluationComponent.BSM,
                                OptionalEvaluationComponent.GBM,
                            ),
                    ),
                ).result

        assertThat(result.action).isEqualTo(EvaluationAction.WARN)
        assertThat(result.issues).isEmpty()
        assertThat(result.violations).noneMatch { it.severity.name == "BLOCK" }
        assertThat(result.abstentions).hasSizeGreaterThanOrEqualTo(4)
    }

    @Test
    fun `each optional model news and disclosure source abstains independently without hold or block`() {
        val cases =
            listOf(
                Triple(
                    "HMM",
                    Harness(
                        signalBundle =
                            SignalMetricBundle(
                                hmmRiskOffProbability = MetricCell.Missing(MetricIssueCode.MODEL_ABSTAINED),
                                meanReversionAbsoluteZScore =
                                    available(decimal("0.50", MetricUnit.ABS_Z_SCORE), MetricSource.SIGNAL, "a"),
                            ),
                    ),
                    setOf(OptionalEvaluationComponent.HMM),
                ),
                Triple("LIGHTGBM", Harness(), setOf(OptionalEvaluationComponent.LIGHTGBM)),
                Triple("BSM", Harness(), setOf(OptionalEvaluationComponent.BSM)),
                Triple("GBM", Harness(), setOf(OptionalEvaluationComponent.GBM)),
                Triple(
                    "NEGATIVE_NEWS",
                    Harness(newsCell = MetricCell.Missing(MetricIssueCode.SOURCE_MISSING)),
                    emptySet(),
                ),
                Triple(
                    "DISCLOSURE",
                    Harness(disclosureCell = MetricCell.Missing(MetricIssueCode.DISCLOSURE_UNAVAILABLE)),
                    emptySet(),
                ),
            )

        cases.forEach { (component, harness, requested) ->
            val result = harness.useCase.evaluate(harness.command(optionalComponents = requested)).result
            val warningKeys = result.warnings.map { it.ruleId to it.publicCode }.toSet()
            val abstentionKeys =
                result.abstentions
                    .filter { it.disposition.name == "ABSTAIN" }
                    .map { it.ruleId to it.publicCode }
                    .toSet()

            assertThat(result.action).describedAs(component).isEqualTo(EvaluationAction.WARN)
            assertThat(result.issues).describedAs(component).isEmpty()
            assertThat(result.violations).describedAs(component).noneMatch { it.severity.name == "BLOCK" }
            assertThat(warningKeys).describedAs(component).isEqualTo(abstentionKeys)
            assertThat(warningKeys).describedAs(component).isNotEmpty()
        }
    }

    @Test
    fun `optional evidence availability that changes the result also changes both hashes`() {
        val availableEvidence =
            Harness(
                signalBundle =
                    SignalMetricBundle(
                        hmmRiskOffProbability =
                            available(decimal("0.10", MetricUnit.RATIO), MetricSource.SIGNAL, "9"),
                        meanReversionAbsoluteZScore =
                            available(decimal("0.50", MetricUnit.ABS_Z_SCORE), MetricSource.SIGNAL, "a"),
                        optionalComponents =
                            listOf(
                                OptionalComponentEvidence(
                                    componentId = OptionalEvaluationComponent.LIGHTGBM.name,
                                    available = true,
                                    reasonCode = null,
                                ),
                            ),
                    ),
            )
        val missingEvidence =
            Harness(
                signalBundle =
                    SignalMetricBundle(
                        hmmRiskOffProbability =
                            available(decimal("0.10", MetricUnit.RATIO), MetricSource.SIGNAL, "9"),
                        meanReversionAbsoluteZScore =
                            available(decimal("0.50", MetricUnit.ABS_Z_SCORE), MetricSource.SIGNAL, "a"),
                        optionalComponents =
                            listOf(
                                OptionalComponentEvidence(
                                    componentId = OptionalEvaluationComponent.LIGHTGBM.name,
                                    available = false,
                                    reasonCode = "SOURCE_MISSING",
                                ),
                            ),
                    ),
            )

        val availableResult =
            availableEvidence.useCase.evaluate(
                availableEvidence.command(optionalComponents = setOf(OptionalEvaluationComponent.LIGHTGBM)),
            )
        val missingResult =
            missingEvidence.useCase.evaluate(
                missingEvidence.command(optionalComponents = setOf(OptionalEvaluationComponent.LIGHTGBM)),
            )

        assertThat(availableResult.result.action).isEqualTo(EvaluationAction.ALLOW)
        assertThat(missingResult.result.action).isEqualTo(EvaluationAction.WARN)
        assertThat(availableResult.semanticInputHash).isNotEqualTo(missingResult.semanticInputHash)
        assertThat(availableResult.snapshotArtifactHash).isNotEqualTo(missingResult.snapshotArtifactHash)
    }

    @Test
    fun `evaluation id changes only artifact identity and leaves semantic outcome unchanged`() {
        val left = Harness()
        val right = Harness()

        val leftResult = left.useCase.evaluate(left.command(evaluationId = "evl_left"))
        val rightResult = right.useCase.evaluate(right.command(evaluationId = "evl_right"))

        assertThat(leftResult.result).isEqualTo(rightResult.result)
        assertThat(leftResult.semanticInputHash).isEqualTo(rightResult.semanticInputHash)
        assertThat(leftResult.snapshotArtifactHash).isNotEqualTo(rightResult.snapshotArtifactHash)
    }

    @Test
    fun `duplicate optional component evidence is rejected before last-wins mapping`() {
        val duplicate =
            OptionalComponentEvidence(
                componentId = OptionalEvaluationComponent.LIGHTGBM.name,
                available = false,
                reasonCode = "SOURCE_MISSING",
            )

        assertThrows<IllegalArgumentException> {
            SignalMetricBundle(
                hmmRiskOffProbability = MetricCell.NotApplicable(MetricIssueCode.NOT_APPLICABLE),
                meanReversionAbsoluteZScore = MetricCell.NotApplicable(MetricIssueCode.NOT_APPLICABLE),
                optionalComponents = listOf(duplicate, duplicate.copy(reasonCode = "SOURCE_ERROR")),
            )
        }
    }

    @Test
    fun `generic signal evidence cannot conflict with a dedicated typed component`() {
        assertThrows<IllegalArgumentException> {
            SignalMetricBundle(
                hmmRiskOffProbability = MetricCell.Missing(MetricIssueCode.MODEL_ABSTAINED),
                meanReversionAbsoluteZScore = MetricCell.NotApplicable(MetricIssueCode.NOT_APPLICABLE),
                optionalComponents =
                    listOf(
                        OptionalComponentEvidence(
                            componentId = OptionalEvaluationComponent.HMM.name,
                            available = true,
                            reasonCode = null,
                        ),
                    ),
            )
        }
    }

    @Test
    fun `owner source and instrument crossing are internal failures rather than business HOLD`() {
        val wrongOwner =
            Harness(
                paperBalanceTransform = { balance ->
                    balance.copy(ownerScopeHash = "e".repeat(64))
                },
            )
        val wrongSource =
            Harness(
                paperBalanceTransform = { balance ->
                    balance.copy(source = PortfolioSource.KIS_MOCK)
                },
            )
        val wrongInstrument = Harness(instrumentSymbol = "000660")

        assertThrows<IllegalStateException> { wrongOwner.useCase.evaluate(wrongOwner.command()) }
        assertThrows<IllegalStateException> { wrongSource.useCase.evaluate(wrongSource.command()) }
        assertThrows<IllegalStateException> {
            wrongInstrument.useCase.evaluate(wrongInstrument.command())
        }
    }

    @Test
    fun `gold ETF ETN metadata cannot bypass the parent instrument risk rule`() {
        assertThrows<IllegalArgumentException> {
            InstrumentSnapshot(
                symbol = "005930",
                isEtfEtn = false,
                isGoldEtfEtn = true,
                productRiskScore = null,
                catalogVersion = "instrument-v1",
            )
        }
    }

    @Test
    fun `balance and instrument gold classification mismatch is an invariant failure`() {
        val harness =
            Harness(
                paperBalanceTransform = { balance ->
                    balance.copy(
                        positions =
                            balance.positions.map { position ->
                                if (position.symbol == "005930") {
                                    position.copy(isGoldEtfEtn = true)
                                } else {
                                    position
                                }
                            },
                    )
                },
            )

        assertThrows<IllegalStateException> {
            harness.useCase.evaluate(harness.command())
        }
    }

    @Test
    fun `hard decimal metric scale drift is rejected as unavailable evidence`() {
        val harness =
            Harness(
                dailyLossRateCell =
                    available(
                        MetricValue.Decimal(
                            BigDecimal("-0.01001"),
                            5,
                            MetricUnit.RATIO,
                        ),
                        MetricSource.RISK_SNAPSHOT,
                        "5",
                    ),
            )

        val result = harness.useCase.evaluate(harness.command()).result

        assertThat(result.action).isEqualTo(EvaluationAction.HOLD)
        assertThat(result.issues)
            .anyMatch {
                it.ruleId == "daily_loss_guard" &&
                    it.internalCause == MetricIssueCode.SOURCE_ERROR
            }
    }

    @Test
    fun `every unavailable or mismatched margin state is a hard readiness HOLD`() {
        val futureMargin =
            MetricCell.Available(
                value = whole(0, MetricUnit.KRW),
                observedAt = AS_OF.plusNanos(1),
                retrievedAt = AS_OF.plusNanos(1),
                freshUntil = AS_OF.plusSeconds(60),
                source = MetricSource.INTERNAL,
                sourceRef = "e".repeat(64),
                sourceVersion = "margin-v1",
            )
        val wrongSourceContract =
            available(
                whole(0, MetricUnit.COUNT),
                MetricSource.INTERNAL,
                "e",
            )
        val cases =
            listOf(
                Triple("missing", MetricCell.Missing(MetricIssueCode.SOURCE_MISSING), MetricIssueCode.SOURCE_MISSING),
                Triple(
                    "stale",
                    MetricCell.Stale(
                        observedAt = AS_OF.minusSeconds(61),
                        freshUntil = AS_OF.minusNanos(1),
                        reason = MetricIssueCode.SOURCE_STALE,
                    ),
                    MetricIssueCode.SOURCE_STALE,
                ),
                Triple("error", MetricCell.Error(MetricIssueCode.SOURCE_ERROR), MetricIssueCode.SOURCE_ERROR),
                Triple(
                    "incomplete",
                    MetricCell.Incomplete(MetricIssueCode.BROKERAGE_UNAVAILABLE),
                    MetricIssueCode.BROKERAGE_UNAVAILABLE,
                ),
                Triple(
                    "abstained",
                    MetricCell.Abstained(MetricIssueCode.MODEL_ABSTAINED),
                    MetricIssueCode.MODEL_ABSTAINED,
                ),
                Triple(
                    "not-applicable",
                    MetricCell.NotApplicable(MetricIssueCode.NOT_APPLICABLE),
                    MetricIssueCode.NOT_APPLICABLE,
                ),
                Triple("future-timestamp", futureMargin, MetricIssueCode.SOURCE_FUTURE_TIMESTAMP),
                Triple("source-contract-mismatch", wrongSourceContract, MetricIssueCode.SOURCE_ERROR),
                // S2.2 port 계약은 source/version mismatch를 새 public code 없이 INCOMPLETE cell로 전달한다.
                Triple(
                    "source-version-mismatch",
                    MetricCell.Incomplete(MetricIssueCode.SOURCE_INCOMPLETE),
                    MetricIssueCode.SOURCE_INCOMPLETE,
                ),
            )

        cases.forEach { (name, marginCell, expectedCause) ->
            val harness = Harness(marginCell = marginCell)
            val result = harness.useCase.evaluate(harness.command()).result
            val marginIssue =
                result.issues.single {
                    it.ruleId == "data_freshness_guard" &&
                        it.publicCode.name == "MARGIN_CONTEXT_UNAVAILABLE"
                }

            assertThat(result.action).describedAs(name).isEqualTo(EvaluationAction.HOLD)
            assertThat(marginIssue.internalCause).describedAs(name).isEqualTo(expectedCause)
        }
    }

    @Test
    fun `disabled and not-requested optional inputs do not call their source ports`() {
        val harness =
            Harness(
                principleRuleIds =
                    setOf(
                        "max_position_per_asset",
                        "max_gold_etf_etn_weight",
                        "max_single_order_amount",
                        "daily_loss_guard",
                        "mdd_guard",
                        "max_daily_orders",
                    ),
            )

        harness.useCase.evaluate(harness.command(optionalComponents = emptySet()))

        assertThat(harness.newsCalls).isZero()
        assertThat(harness.disclosureCalls).isZero()
        assertThat(harness.signalCalls).isZero()
    }

    @Test
    fun `margin available only within the evaluation window remains READY`() {
        val harness = Harness()

        val result = harness.useCase.evaluate(harness.command()).result

        assertThat(result.issues)
            .noneMatch {
                it.ruleId == "data_freshness_guard" &&
                    it.publicCode.name == "MARGIN_CONTEXT_UNAVAILABLE"
            }
    }

    @Test
    fun `sparse Principle rules are normalized to canonical disabled dispositions`() {
        val harness = Harness(principleRuleIds = setOf("max_single_order_amount"))

        val result = harness.useCase.evaluate(harness.command()).result

        assertThat(result.action).isEqualTo(EvaluationAction.ALLOW)
        assertThat(result.abstentions)
            .anyMatch {
                it.ruleId == "max_position_per_asset" &&
                    it.disposition.name == "NOT_APPLICABLE"
            }
        assertThat(result.abstentions).noneMatch { it.ruleId == "max_single_order_amount" }
    }

    @Test
    fun `combined provenance over the exact bound is rejected without truncation`() {
        val disclosureRefs =
            (0 until 100).map { index ->
                index.toString(16).padStart(64, '0')
            }
        val harness =
            Harness(
                disclosureCell =
                    available(
                        DisclosureRiskSnapshot(
                            score = BigDecimal("0.10"),
                            mappingVersion = "s1.2-v1",
                            events = emptyList(),
                            warnings = emptyList(),
                            sourceRefs = disclosureRefs,
                        ),
                        MetricSource.OPENDART,
                        "8",
                    ),
            )

        assertThrows<IllegalArgumentException> {
            harness.useCase.evaluate(harness.command())
        }
    }

    private class Harness(
        private val contextAvailable: Boolean = true,
        disclosureRequirement: EvidenceRequirement = EvidenceRequirement.OPTIONAL,
        principleRuleIds: Set<String>? = null,
        private val kisBalanceCell: MetricCell<BalanceSnapshot>? = null,
        private val paperBalanceTransform: (BalanceSnapshot) -> BalanceSnapshot = { it },
        private val instrumentSymbol: String = "005930",
        private val marginCell: MetricCell<MetricValue> =
            available(whole(0, MetricUnit.KRW), MetricSource.INTERNAL, "e"),
        private val dailyLossRateCell: MetricCell<MetricValue>? = null,
        private val newsCell: MetricCell<MetricValue> =
            available(decimal("0.10", MetricUnit.RATIO), MetricSource.NEWS, "d"),
        private val disclosureCell: MetricCell<DisclosureRiskSnapshot> =
            available(
                DisclosureRiskSnapshot(
                    score = BigDecimal("0.10"),
                    mappingVersion = "s1.2-v1",
                    events = listOf(DisclosureEventEvidence("piicDecsn", "ACTIVE")),
                    warnings = emptyList(),
                    sourceRefs = listOf("8".repeat(64)),
                ),
                MetricSource.OPENDART,
                "8",
            ),
        private val signalBundle: SignalMetricBundle =
            SignalMetricBundle(
                hmmRiskOffProbability = available(decimal("0.10", MetricUnit.RATIO), MetricSource.SIGNAL, "9"),
                meanReversionAbsoluteZScore =
                    available(decimal("0.50", MetricUnit.ABS_Z_SCORE), MetricSource.SIGNAL, "a"),
            ),
    ) {
        var principleCalls = 0
        var contextCalls = 0
        var priceCalls = 0
        var kisBalanceCalls = 0
        var paperBalanceCalls = 0
        var marginCalls = 0
        var orderMetricCalls = 0
        var riskCalls = 0
        var instrumentCalls = 0
        var newsCalls = 0
        var disclosureCalls = 0
        var signalCalls = 0

        private val principle =
            principle(disclosureRequirement).let { snapshot ->
                if (principleRuleIds == null) {
                    snapshot
                } else {
                    snapshot.copy(rules = snapshot.rules.filter { it.ruleId in principleRuleIds })
                }
            }
        private val ownerScopeHash = "b".repeat(64)
        private val paperBalance =
            available(
                BalanceSnapshot(
                    source = PortfolioSource.INTERNAL_PAPER,
                    revision = "paper-revision-7",
                    ownerScopeHash = ownerScopeHash,
                    cashKrw = 900_000,
                    portfolioEquityKrw = 1_000_000,
                    positions =
                        listOf(
                            PortfolioPosition(
                                symbol = "005930",
                                quantity = 10,
                                marketValueKrw = 100_000,
                                isGoldEtfEtn = false,
                            ),
                            PortfolioPosition(
                                symbol = "000660",
                                quantity = 99,
                                marketValueKrw = 300_000,
                                isGoldEtfEtn = false,
                            ),
                        ),
                ),
                MetricSource.INTERNAL_PAPER,
                "2",
            )
        private val defaultKisBalance =
            available(
                paperBalance.value.copy(
                    source = PortfolioSource.KIS_MOCK,
                    revision = "kis-revision-7",
                ),
                MetricSource.KIS_MOCK,
                "3",
            )

        val useCase: PortfolioEvaluationUseCase =
            PortfolioEvaluationUseCase(
                principleSnapshotPort =
                    object : PrincipleSnapshotPort {
                        override fun findActiveOwned(
                            actorUserId: String,
                            principleId: PrincipleId,
                        ): ActivePrincipleSnapshot? {
                            principleCalls += 1
                            return principle
                        }
                    },
                portfolioContextPort =
                    object : PortfolioContextPort {
                        override fun resolve(
                            actorUserId: String,
                            source: PortfolioSource,
                        ): PortfolioContextResolution {
                            contextCalls += 1
                            return if (contextAvailable) {
                                PortfolioContextResolution.Available(
                                    PortfolioContextRef(
                                        opaqueRef = "server-owned-context",
                                        source = source,
                                        ownerScopeHash = ownerScopeHash,
                                    ),
                                )
                            } else {
                                PortfolioContextResolution.Unavailable(
                                    com.capstone.decision.application.risk.port
                                        .PortfolioContextUnavailableReason.MISSING,
                                )
                            }
                        }
                    },
                snapshotAssembler =
                    MetricSnapshotAssembler(
                        pricePort =
                            object : PricePort {
                                override fun load(request: EvaluationSourceRequest): MetricCell<MetricValue> {
                                    priceCalls += 1
                                    return available(whole(10_000, MetricUnit.KRW), MetricSource.INTERNAL, "1")
                                }
                            },
                        kisMockBalancePort =
                            countingBalance(PortfolioSource.KIS_MOCK) {
                                kisBalanceCalls += 1
                                kisBalanceCell ?: defaultKisBalance
                            },
                        internalPaperBalancePort =
                            countingBalance(PortfolioSource.INTERNAL_PAPER) {
                                paperBalanceCalls += 1
                                paperBalance.copy(value = paperBalanceTransform(paperBalance.value))
                            },
                        marginPort =
                            object : MarginPort {
                                override fun load(request: EvaluationSourceRequest): MetricCell<MetricValue> {
                                    marginCalls += 1
                                    return marginCell
                                }
                            },
                        orderMetricPort =
                            object : OrderMetricPort {
                                override fun loadDailyOrderCount(request: EvaluationSourceRequest): MetricCell<MetricValue> {
                                    orderMetricCalls += 1
                                    return available(whole(0, MetricUnit.COUNT), MetricSource.INTERNAL, "4")
                                }
                            },
                        riskSnapshotPort =
                            object : RiskSnapshotPort {
                                override fun load(request: EvaluationSourceRequest): RiskMetricBundle {
                                    riskCalls += 1
                                    return RiskMetricBundle(
                                        dailyLossRate =
                                            dailyLossRateCell
                                                ?: available(
                                                    decimal("-0.01", MetricUnit.RATIO),
                                                    MetricSource.RISK_SNAPSHOT,
                                                    "5",
                                                ),
                                        maxDrawdown =
                                            available(
                                                decimal("-0.05", MetricUnit.RATIO),
                                                MetricSource.RISK_SNAPSHOT,
                                                "6",
                                            ),
                                        annualizedVolatility =
                                            available(
                                                decimal("0.10", MetricUnit.RATIO),
                                                MetricSource.RISK_SNAPSHOT,
                                                "7",
                                            ),
                                    )
                                }
                            },
                        instrumentCatalogPort =
                            object : InstrumentCatalogPort {
                                override fun load(request: EvaluationSourceRequest): MetricCell<InstrumentSnapshot> {
                                    instrumentCalls += 1
                                    return available(
                                        InstrumentSnapshot(
                                            symbol = instrumentSymbol,
                                            isEtfEtn = false,
                                            isGoldEtfEtn = false,
                                            productRiskScore = null,
                                            catalogVersion = "instrument-v1",
                                        ),
                                        MetricSource.INSTRUMENT_CATALOG,
                                        "c",
                                    )
                                }
                            },
                        newsEvidencePort =
                            object : NewsEvidencePort {
                                override fun loadNegativeScore(request: EvaluationSourceRequest): MetricCell<MetricValue> {
                                    newsCalls += 1
                                    return newsCell
                                }
                            },
                        disclosureRiskPort =
                            object : DisclosureRiskPort {
                                override fun load(request: EvaluationSourceRequest): MetricCell<DisclosureRiskSnapshot> {
                                    disclosureCalls += 1
                                    return disclosureCell
                                }
                            },
                        signalPort =
                            object : SignalPort {
                                override fun load(request: EvaluationSourceRequest): SignalMetricBundle {
                                    signalCalls += 1
                                    return signalBundle
                                }
                            },
                    ),
                systemRuleContract =
                    ClasspathSystemRuleCatalog(JsonMapper.builder().build()),
            )

        fun command(
            portfolioSource: String = PortfolioSource.INTERNAL_PAPER.name,
            optionalComponents: Set<OptionalEvaluationComponent> = emptySet(),
            evaluationId: String = "evl_0123456789abcdef",
        ): PortfolioEvaluationCommand =
            PortfolioEvaluationCommand(
                actorUserId = "usr_demo_user",
                principleId = principle.principleId,
                portfolioSource = portfolioSource,
                evaluationId = evaluationId,
                evaluationAsOf = AS_OF,
                orderIntent =
                    OrderIntentSnapshot(
                        symbol = "005930",
                        side = "BUY",
                        orderType = "MARKET",
                        quantity = 1,
                        limitPrice = null,
                    ),
                optionalComponents = optionalComponents,
            )

        fun sourceCalls(): Int = allSourceCallCounts().sum()

        fun allSourceCallCounts(): List<Int> =
            listOf(
                priceCalls,
                kisBalanceCalls,
                paperBalanceCalls,
                marginCalls,
                orderMetricCalls,
                riskCalls,
                instrumentCalls,
                newsCalls,
                disclosureCalls,
                signalCalls,
            )

        private fun countingBalance(
            selectedSource: PortfolioSource,
            result: () -> MetricCell<BalanceSnapshot>,
        ): BalancePort =
            object : BalancePort {
                override val source: PortfolioSource = selectedSource

                override fun load(request: EvaluationSourceRequest): MetricCell<BalanceSnapshot> = result()
            }
    }

    companion object {
        private val AS_OF = Instant.parse("2030-01-02T03:04:05Z")

        private fun principle(disclosureRequirement: EvidenceRequirement): ActivePrincipleSnapshot =
            ActivePrincipleSnapshot(
                principleId = PrincipleId("prc_0123456789abcdef0123456789abcdef"),
                principleVersionId = PrincipleVersionId("pvr_0123456789abcdef0123456789abcdef"),
                version = 3,
                mode = PrincipleMode.GUIDE,
                rules =
                    listOf(
                        rule("max_position_per_asset", "POSITION_LIMIT", "asset_weight", "<=", "0.50"),
                        rule("max_gold_etf_etn_weight", "POSITION_LIMIT", "gold_etf_etn_weight", "<=", "0.50"),
                        rule("max_single_order_amount", "ORDER_SIZE", "order_amount_krw", "<=", "1000000"),
                        rule("daily_loss_guard", "LOSS_LIMIT", "daily_loss_rate", ">=", "-0.05"),
                        rule("mdd_guard", "DRAWDOWN_LIMIT", "mdd", ">=", "-0.20"),
                        rule("max_daily_orders", "TRADING_FREQUENCY", "daily_order_count", "<=", "10"),
                        rule(
                            "negative_news_guard",
                            "NEWS_GUARD",
                            "negative_news_score",
                            "<=",
                            "0.80",
                            EvidenceRequirement.OPTIONAL,
                        ),
                        rule(
                            "disclosure_risk_guard",
                            "DISCLOSURE_GUARD",
                            "disclosure_risk_score",
                            "<=",
                            "0.80",
                            disclosureRequirement,
                        ),
                    ),
            )

        private fun rule(
            ruleId: String,
            ruleType: String,
            metric: String,
            operator: String,
            threshold: String,
            requirement: EvidenceRequirement = EvidenceRequirement.REQUIRED,
        ): PrincipleRule =
            PrincipleRule(
                ruleId = ruleId,
                ruleType = ruleType,
                metric = metric,
                operator = operator,
                threshold = BigDecimal(threshold),
                severity = "BLOCK",
                enabled = true,
                evidenceRequirement = requirement,
            )

        private fun whole(
            value: Long,
            unit: MetricUnit,
        ): MetricValue = MetricValue.Whole(value, unit)

        private fun decimal(
            value: String,
            unit: MetricUnit,
        ): MetricValue = MetricValue.Decimal(BigDecimal(value), 4, unit)

        private fun <T> available(
            value: T,
            source: MetricSource,
            refCharacter: String,
        ): MetricCell.Available<T> =
            MetricCell.Available(
                value = value,
                observedAt = AS_OF.minusSeconds(1),
                retrievedAt = AS_OF,
                freshUntil = AS_OF.plusSeconds(60),
                source = source,
                sourceRef = refCharacter.repeat(64),
                sourceVersion = "fixture-v1",
            )
    }
}
