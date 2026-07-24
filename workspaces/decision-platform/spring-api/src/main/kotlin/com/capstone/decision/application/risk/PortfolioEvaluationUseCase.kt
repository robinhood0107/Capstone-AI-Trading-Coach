package com.capstone.decision.application.risk

import com.capstone.decision.application.risk.port.ActivePrincipleSnapshot
import com.capstone.decision.application.risk.port.OptionalComponentEvidence
import com.capstone.decision.application.risk.port.PortfolioContextPort
import com.capstone.decision.application.risk.port.PortfolioContextResolution
import com.capstone.decision.application.risk.port.PrincipleSnapshotPort
import com.capstone.decision.domain.principle.EvidenceRequirement
import com.capstone.decision.domain.principle.PrincipleId
import com.capstone.decision.domain.principle.PrincipleNotFoundException
import com.capstone.decision.domain.principle.PrincipleRule
import com.capstone.decision.domain.principle.PrincipleValidationException
import com.capstone.decision.domain.principle.PrincipleViolation
import com.capstone.decision.domain.risk.Abstention
import com.capstone.decision.domain.risk.CandidateRule
import com.capstone.decision.domain.risk.CanonicalEvaluationRuleSet
import com.capstone.decision.domain.risk.CanonicalJson
import com.capstone.decision.domain.risk.DeterministicAggregator
import com.capstone.decision.domain.risk.EvaluationAction
import com.capstone.decision.domain.risk.EvaluationIssue
import com.capstone.decision.domain.risk.EvaluationResult
import com.capstone.decision.domain.risk.EvaluationWarning
import com.capstone.decision.domain.risk.EvidenceDisposition
import com.capstone.decision.domain.risk.MetricCell
import com.capstone.decision.domain.risk.MetricIssueCode
import com.capstone.decision.domain.risk.MetricKey
import com.capstone.decision.domain.risk.MetricSnapshot
import com.capstone.decision.domain.risk.MetricValue
import com.capstone.decision.domain.risk.OfflineRuleEvaluationEngine
import com.capstone.decision.domain.risk.OrderIntentSnapshot
import com.capstone.decision.domain.risk.PortfolioSnapshotIdentity
import com.capstone.decision.domain.risk.PortfolioSource
import com.capstone.decision.domain.risk.PrincipleSnapshotIdentity
import com.capstone.decision.domain.risk.PublicEvidenceCode
import com.capstone.decision.domain.risk.PublicIssueCode
import com.capstone.decision.domain.risk.RuleExecutionKind
import com.capstone.decision.domain.risk.RuleOperator
import com.capstone.decision.domain.risk.RuleOrigin
import com.capstone.decision.domain.risk.RuleOutcome
import com.capstone.decision.domain.risk.RuleSeverity
import com.capstone.decision.domain.risk.SnapshotHashService
import java.math.BigDecimal
import java.time.Instant

enum class OptionalEvaluationComponent {
    LIGHTGBM,
    HMM,
    MEAN_REVERSION,
    BSM,
    GBM,
}

data class PortfolioEvaluationCommand(
    val actorUserId: String,
    val principleId: PrincipleId,
    val portfolioSource: String,
    val evaluationId: String,
    val evaluationAsOf: Instant,
    val orderIntent: OrderIntentSnapshot,
    val optionalComponents: Set<OptionalEvaluationComponent> = emptySet(),
    val decisionId: String = evaluationId,
)

data class OfflinePortfolioEvaluation(
    val principle: ActivePrincipleSnapshot,
    val portfolioSource: PortfolioSource,
    val result: EvaluationResult,
    val snapshot: MetricSnapshot?,
    val semanticInputHash: String?,
    val snapshotArtifactHash: String?,
)

/**
 * S2.2의 annotation-free offline orchestration 경계다. 서버가 owner-scoped context를 고르고
 * 외부 source 호출과 결과 persistence/HTTP 변환은 각각 S3와 S2.3 이후 adapter에 맡긴다.
 */
class PortfolioEvaluationUseCase(
    private val principleSnapshotPort: PrincipleSnapshotPort,
    private val portfolioContextPort: PortfolioContextPort,
    private val snapshotAssembler: MetricSnapshotAssembler,
    private val systemRuleContract: SystemRuleContract,
    private val evaluationEngine: OfflineRuleEvaluationEngine = OfflineRuleEvaluationEngine(),
    private val aggregator: DeterministicAggregator = DeterministicAggregator(),
    private val hashService: SnapshotHashService = SnapshotHashService(),
) {
    fun evaluate(command: PortfolioEvaluationCommand): OfflinePortfolioEvaluation {
        // selector가 틀리면 owner lookup이나 source port를 한 번도 호출하지 않는다.
        parsePortfolioSource(command.portfolioSource)
        val principle =
            principleSnapshotPort.findActiveOwned(command.actorUserId, command.principleId)
                ?: throw PrincipleNotFoundException()
        return evaluatePinned(command, principle)
    }

    /**
     * S2.3 HTTP orchestration이 한 SQL로 pin한 immutable Principle을 재사용해 source read 전에 이중 조회를 만들지 않는다.
     */
    fun evaluatePinned(
        command: PortfolioEvaluationCommand,
        principle: ActivePrincipleSnapshot,
    ): OfflinePortfolioEvaluation {
        val source = parsePortfolioSource(command.portfolioSource)
        check(principle.principleId == command.principleId) {
            "Pinned Principle does not match the evaluation selector."
        }
        return when (val resolution = portfolioContextPort.resolve(command.actorUserId, source)) {
            is PortfolioContextResolution.Unavailable ->
                contextUnavailable(command, principle, source)

            is PortfolioContextResolution.Available -> {
                check(resolution.context.source == source) {
                    "Portfolio context source crossed the explicit selector."
                }
                evaluateAvailableContext(command, principle, resolution.context)
            }
        }
    }

    private fun evaluateAvailableContext(
        command: PortfolioEvaluationCommand,
        principle: ActivePrincipleSnapshot,
        context: com.capstone.decision.application.risk.port.PortfolioContextRef,
    ): OfflinePortfolioEvaluation {
        val plan = acquisitionPlan(principle, command.optionalComponents)
        val snapshot =
            snapshotAssembler.assemble(
                MetricAssemblyRequest(
                    actorUserId = command.actorUserId,
                    evaluationId = command.evaluationId,
                    evaluationAsOf = command.evaluationAsOf,
                    principle = principle,
                    portfolioContext = context,
                    orderIntent = command.orderIntent,
                    systemRuleCatalogVersion = systemRuleContract.catalogVersion,
                    readinessPolicyVersion = systemRuleContract.readinessPolicyVersion,
                    acquisitionPlan = plan,
                    decisionId = command.decisionId,
                ),
            )
        val candidates = candidateRules(principle, snapshot, command.optionalComponents)
        val ruleResult =
            evaluationEngine.evaluate(
                CanonicalEvaluationRuleSet.of(candidates),
                snapshot,
            )
        val outcomes =
            outcomes(ruleResult) +
                hardReadinessOutcomes(snapshot, candidates) +
                optionalComponentOutcomes(
                    snapshot.requestedOptionalComponents,
                    snapshot.observedOptionalComponentEvidence,
                )
        val result = aggregator.aggregate(outcomes)
        return OfflinePortfolioEvaluation(
            principle = principle,
            portfolioSource = context.source,
            result = result,
            snapshot = snapshot,
            semanticInputHash = hashService.semanticInputHash(snapshot),
            snapshotArtifactHash = hashService.snapshotArtifactHash(snapshot),
        )
    }

    private fun acquisitionPlan(
        principle: ActivePrincipleSnapshot,
        optionalComponents: Set<OptionalEvaluationComponent>,
    ): MetricAcquisitionPlan {
        val enabledPublicMetrics =
            principle.rules
                .filter(PrincipleRule::enabled)
                .map { MetricKey.fromWire(it.metric) }
        val requestedSystemMetrics =
            buildSet {
                add(MetricKey.ETF_ETN_RISK_SCORE)
                if (OptionalEvaluationComponent.HMM in optionalComponents) {
                    add(MetricKey.HMM_RISK_OFF_PROBABILITY)
                }
                if (OptionalEvaluationComponent.MEAN_REVERSION in optionalComponents) {
                    add(MetricKey.MEAN_REVERSION_Z_SCORE)
                }
            }
        return MetricAcquisitionPlan(
            metricKeys = (HARD_READINESS_METRICS + enabledPublicMetrics + requestedSystemMetrics).toSet(),
            optionalComponents = requestedOptionalComponentIds(principle, optionalComponents),
        )
    }

    private fun requestedOptionalComponentIds(
        principle: ActivePrincipleSnapshot,
        optionalComponents: Set<OptionalEvaluationComponent>,
    ): Set<String> =
        buildSet {
            addAll(optionalComponents.map(OptionalEvaluationComponent::name))
            principle.rules
                .filter { it.enabled && it.evidenceRequirement == EvidenceRequirement.OPTIONAL }
                .forEach { rule ->
                    when (rule.ruleId) {
                        "negative_news_guard" -> add(NEWS_COMPONENT)
                        "disclosure_risk_guard" -> add(DISCLOSURE_COMPONENT)
                    }
                }
        }

    private fun candidateRules(
        principle: ActivePrincipleSnapshot,
        snapshot: MetricSnapshot,
        optionalComponents: Set<OptionalEvaluationComponent>,
    ): List<CandidateRule> {
        val principleRules = principle.rules.associateBy(PrincipleRule::ruleId)
        val publicCatalogRules =
            systemRuleContract.rules.filter {
                it.ownership == CatalogRuleOwnership.PUBLIC_PRINCIPLE
            }
        val publicRuleIds = publicCatalogRules.map(CatalogEvaluationRule::ruleId).toSet()
        check(principleRules.size == principle.rules.size && principleRules.keys.all(publicRuleIds::contains)) {
            "Pinned Principle rule set contains duplicate or unknown rules."
        }
        return systemRuleContract.rules.map { catalog ->
            when (catalog.ownership) {
                CatalogRuleOwnership.PUBLIC_PRINCIPLE ->
                    publicCandidate(catalog, principleRules[catalog.ruleId])

                CatalogRuleOwnership.SYSTEM_MANAGED ->
                    systemCandidate(catalog, snapshot, optionalComponents)
            }
        }
    }

    private fun publicCandidate(
        catalog: CatalogEvaluationRule,
        principleRule: PrincipleRule?,
    ): CandidateRule {
        if (principleRule == null) {
            // S2.1은 1..8 sparse rule set을 허용한다. 빠진 rule은 추측 임계값 없이 disabled N/A로 정규화한다.
            return CandidateRule(
                order = catalog.order,
                ruleId = catalog.ruleId,
                metricKey = MetricKey.fromWire(catalog.metric),
                operator = RuleOperator.fromWire(requireNotNull(catalog.operator)),
                threshold = BigDecimal.ZERO,
                thresholdScale = requireNotNull(catalog.scale),
                severity = RuleSeverity.WARN,
                evidenceRequirement =
                    if (catalog.evidenceCriticality == CatalogEvidenceCriticality.HARD) {
                        EvidenceRequirement.REQUIRED
                    } else {
                        EvidenceRequirement.OPTIONAL
                    },
                enabled = false,
                applicable = true,
                origin = RuleOrigin.PUBLIC_PRINCIPLE,
                executionKind = RuleExecutionKind.THRESHOLD,
            )
        }
        check(principleRule.metric == catalog.metric)
        check(principleRule.operator == catalog.operator)
        return CandidateRule(
            order = catalog.order,
            ruleId = catalog.ruleId,
            metricKey = MetricKey.fromWire(catalog.metric),
            operator = RuleOperator.fromWire(principleRule.operator),
            threshold = principleRule.threshold,
            thresholdScale = requireNotNull(catalog.scale),
            severity = severity(principleRule.severity, principleRule.enabled),
            evidenceRequirement = principleRule.evidenceRequirement,
            enabled = principleRule.enabled,
            applicable = true,
            origin = RuleOrigin.PUBLIC_PRINCIPLE,
            executionKind = RuleExecutionKind.THRESHOLD,
        )
    }

    private fun systemCandidate(
        catalog: CatalogEvaluationRule,
        snapshot: MetricSnapshot,
        optionalComponents: Set<OptionalEvaluationComponent>,
    ): CandidateRule {
        val executionKind = RuleExecutionKind.valueOf(catalog.executionKind)
        val applicable =
            when (catalog.applicability) {
                CatalogApplicability.ALWAYS,
                CatalogApplicability.SOURCE_APPLICABLE,
                -> true

                CatalogApplicability.MODEL_REQUESTED ->
                    when (catalog.ruleId) {
                        HMM_RULE_ID -> OptionalEvaluationComponent.HMM in optionalComponents
                        MEAN_REVERSION_RULE_ID -> OptionalEvaluationComponent.MEAN_REVERSION in optionalComponents
                        else -> false
                    }

                CatalogApplicability.ORDER_INSTRUMENT_APPLICABLE ->
                    // 적용 여부도 hash 대상 snapshot state에서만 읽어 별도 decision side channel을 만들지 않는다.
                    snapshot.metric(MetricKey.ETF_ETN_RISK_SCORE) !is MetricCell.NotApplicable

                CatalogApplicability.NOT_APPLICABLE_V1 -> false
                CatalogApplicability.PRINCIPLE_RULE_ENABLED ->
                    error("System rule cannot use Principle-owned applicability.")
            }
        return CandidateRule(
            order = catalog.order,
            ruleId = catalog.ruleId,
            metricKey = systemMetricKey(catalog),
            operator = catalog.operator?.let(RuleOperator::fromWire) ?: RuleOperator.LESS_THAN_OR_EQUAL,
            threshold = catalog.defaultThreshold ?: BigDecimal.ZERO,
            thresholdScale = catalog.scale ?: 0,
            severity = catalog.defaultSeverity?.let(RuleSeverity::valueOf) ?: RuleSeverity.WARN,
            evidenceRequirement =
                if (catalog.evidenceCriticality == CatalogEvidenceCriticality.HARD) {
                    EvidenceRequirement.REQUIRED
                } else {
                    EvidenceRequirement.OPTIONAL
                },
            enabled = true,
            applicable = applicable,
            origin = RuleOrigin.SYSTEM_MANAGED,
            executionKind = executionKind,
        )
    }

    private fun hardReadinessOutcomes(
        snapshot: MetricSnapshot,
        candidates: List<CandidateRule>,
    ): List<RuleOutcome> {
        val directlyEvaluatedRequiredMetrics =
            candidates
                .filter {
                    it.enabled &&
                        it.applicable &&
                        it.executionKind == RuleExecutionKind.THRESHOLD &&
                        it.evidenceRequirement == EvidenceRequirement.REQUIRED
                }.map(CandidateRule::metricKey)
                .toSet()
        return HARD_READINESS_METRICS.mapNotNull { key ->
            val cell = snapshot.metric(key)
            when {
                key in directlyEvaluatedRequiredMetrics -> null
                cell is MetricCell.Available &&
                    !cell.observedAt.isAfter(snapshot.evaluationAsOf) &&
                    !snapshot.evaluationAsOf.isAfter(cell.freshUntil) -> null

                else ->
                    RuleOutcome.Hold(
                        EvaluationIssue(
                            order = DATA_FRESHNESS_ORDER,
                            ruleId = DATA_FRESHNESS_RULE_ID,
                            publicCode = hardIssueCode(key, cell),
                            internalCause = internalCause(cell, snapshot.evaluationAsOf),
                            message = "A required evaluation input is unavailable.",
                            source = key.wireName,
                        ),
                    )
            }
        }
    }

    private fun optionalComponentOutcomes(
        requested: List<String>,
        observed: List<OptionalComponentEvidence>,
    ): List<RuleOutcome> =
        requested
            .filter(GENERIC_OPTIONAL_COMPONENTS::contains)
            .sorted()
            .flatMap { component ->
                val evidence = observed.firstOrNull { it.componentId == component }
                if (evidence?.available == true) {
                    emptyList()
                } else {
                    val publicCode = optionalEvidenceCode(evidence?.reasonCode)
                    val internalCause = optionalInternalCause(evidence?.reasonCode)
                    listOf(
                        RuleOutcome.Warning(
                            EvaluationWarning(
                                order = DATA_FRESHNESS_ORDER,
                                ruleId = DATA_FRESHNESS_RULE_ID,
                                publicCode = publicCode,
                                internalCause = internalCause,
                                message = "Optional evidence was not used for this evaluation.",
                                source = component,
                            ),
                        ),
                        RuleOutcome.Abstained(
                            Abstention(
                                order = DATA_FRESHNESS_ORDER,
                                ruleId = DATA_FRESHNESS_RULE_ID,
                                publicCode = publicCode,
                                internalCause = internalCause,
                                disposition = EvidenceDisposition.ABSTAIN,
                                message = "Optional evidence was not used for this evaluation.",
                                component = component,
                            ),
                        ),
                    )
                }
            }

    private fun optionalEvidenceCode(reasonCode: String?): PublicEvidenceCode =
        when {
            reasonCode == MetricIssueCode.MODEL_ABSTAINED.name -> PublicEvidenceCode.MODEL_ABSTAINED
            reasonCode?.contains("STALE") == true ||
                reasonCode == MetricIssueCode.SOURCE_FUTURE_TIMESTAMP.name ->
                PublicEvidenceCode.OPTIONAL_EVIDENCE_STALE

            reasonCode?.contains("ERROR") == true -> PublicEvidenceCode.OPTIONAL_EVIDENCE_ERROR
            reasonCode?.contains("INCOMPLETE") == true -> PublicEvidenceCode.OPTIONAL_EVIDENCE_INCOMPLETE
            else -> PublicEvidenceCode.OPTIONAL_EVIDENCE_MISSING
        }

    private fun optionalInternalCause(reasonCode: String?): MetricIssueCode =
        MetricIssueCode.entries.singleOrNull { it.name == reasonCode }
            ?: MetricIssueCode.SOURCE_MISSING

    private fun contextUnavailable(
        command: PortfolioEvaluationCommand,
        principle: ActivePrincipleSnapshot,
        source: PortfolioSource,
    ): OfflinePortfolioEvaluation {
        // early business HOLD도 wire hash 계약을 만족하도록 provider evidence가 없는 snapshot을 남긴다.
        val snapshot =
            MetricSnapshot(
                snapshotSchemaVersion = "s2.2-metric-snapshot-v2",
                evaluationId = command.evaluationId,
                evaluationAsOf = command.evaluationAsOf,
                retrievedAt = command.evaluationAsOf,
                actorUserId = command.actorUserId,
                principle =
                    PrincipleSnapshotIdentity(
                        principleId = principle.principleId.value,
                        principleVersionId = principle.principleVersionId.value,
                        version = principle.version,
                        mode = principle.mode,
                        rulesHash = principleRulesHash(principle),
                    ),
                systemRuleCatalogVersion = systemRuleContract.catalogVersion,
                readinessPolicyVersion = systemRuleContract.readinessPolicyVersion,
                portfolio =
                    PortfolioSnapshotIdentity(
                        source = source,
                        revision = "unavailable-${source.name.lowercase()}",
                        ownerScopeHash =
                            CanonicalJson.sha256(
                                "s2.2-context-unavailable|${command.actorUserId}|${source.name}",
                            ),
                        positionCount = 0,
                    ),
                orderIntent = command.orderIntent,
                metrics = emptyMap(),
                provenanceRefs = emptyList(),
                requestedOptionalComponents =
                    requestedOptionalComponentIds(principle, command.optionalComponents).sorted(),
            )
        val result =
            EvaluationResult(
                action = EvaluationAction.HOLD,
                violations = emptyList(),
                issues =
                    listOf(
                        EvaluationIssue(
                            order = DATA_FRESHNESS_ORDER,
                            ruleId = DATA_FRESHNESS_RULE_ID,
                            publicCode = PublicIssueCode.PORTFOLIO_CONTEXT_UNAVAILABLE,
                            internalCause = MetricIssueCode.PORTFOLIO_CONTEXT_UNAVAILABLE,
                            message = "The owner-scoped portfolio context is unavailable.",
                            source = "portfolio_context",
                        ),
                    ),
                warnings = emptyList(),
                abstentions = emptyList(),
            )
        return OfflinePortfolioEvaluation(
            principle = principle,
            portfolioSource = source,
            result = result,
            snapshot = snapshot,
            semanticInputHash = hashService.semanticInputHash(snapshot),
            snapshotArtifactHash = hashService.snapshotArtifactHash(snapshot),
        )
    }

    private fun parsePortfolioSource(value: String): PortfolioSource =
        try {
            PortfolioSource.parse(value)
        } catch (_: IllegalArgumentException) {
            throw PrincipleValidationException(
                listOf(PrincipleViolation("/portfolioSource", "INVALID_ENUM")),
            )
        }

    private fun severity(
        value: String,
        enabled: Boolean,
    ): RuleSeverity =
        if (!enabled && value == "ALLOW") {
            RuleSeverity.WARN
        } else {
            RuleSeverity.valueOf(value)
        }

    private fun systemMetricKey(catalog: CatalogEvaluationRule): MetricKey =
        if (catalog.executionKind == RuleExecutionKind.THRESHOLD.name) {
            MetricKey.fromWire(catalog.metric)
        } else {
            MetricKey.CURRENT_PRICE_KRW
        }

    private fun outcomes(result: EvaluationResult): List<RuleOutcome> =
        result.violations.map(RuleOutcome::Violated) +
            result.issues.map(RuleOutcome::Hold) +
            result.warnings.map(RuleOutcome::Warning) +
            result.abstentions.map(RuleOutcome::Abstained)

    private fun hardIssueCode(
        key: MetricKey,
        cell: MetricCell<MetricValue>,
    ): PublicIssueCode =
        when (key) {
            MetricKey.CURRENT_PRICE_KRW ->
                if (cell is MetricCell.Stale || staleReason(cell)) {
                    PublicIssueCode.PRICE_STALE
                } else {
                    PublicIssueCode.PRICE_MISSING
                }

            MetricKey.OWNER_POSITION_QUANTITY,
            MetricKey.PORTFOLIO_EQUITY_KRW,
            ->
                when {
                    cell is MetricCell.Stale || staleReason(cell) -> PublicIssueCode.BALANCE_STALE
                    cell is MetricCell.Incomplete -> PublicIssueCode.BALANCE_PARTIAL
                    else -> PublicIssueCode.BROKERAGE_UNAVAILABLE
                }

            MetricKey.MARGIN_REQUIREMENT_KRW -> PublicIssueCode.MARGIN_CONTEXT_UNAVAILABLE
            MetricKey.DAILY_LOSS_RATE,
            MetricKey.MDD,
            MetricKey.ANNUALIZED_VOLATILITY,
            ->
                if (cell is MetricCell.Incomplete) {
                    PublicIssueCode.RISK_SNAPSHOT_VERSION_MISMATCH
                } else {
                    PublicIssueCode.RISK_SNAPSHOT_MISSING
                }

            else -> PublicIssueCode.SOURCE_DEADLINE_EXCEEDED
        }

    private fun internalCause(
        cell: MetricCell<MetricValue>,
        evaluationAsOf: Instant,
    ): MetricIssueCode =
        when (cell) {
            is MetricCell.Missing -> cell.reason
            is MetricCell.Stale -> cell.reason
            is MetricCell.Error -> cell.reason
            is MetricCell.Incomplete -> cell.reason
            is MetricCell.Abstained -> cell.reason
            is MetricCell.NotApplicable -> cell.reason
            is MetricCell.Available ->
                if (cell.observedAt.isAfter(evaluationAsOf)) {
                    MetricIssueCode.SOURCE_FUTURE_TIMESTAMP
                } else {
                    MetricIssueCode.SOURCE_STALE
                }
        }

    private fun staleReason(cell: MetricCell<MetricValue>): Boolean =
        when (cell) {
            is MetricCell.Missing -> cell.reason == MetricIssueCode.SOURCE_STALE
            is MetricCell.Error -> cell.reason == MetricIssueCode.SOURCE_STALE
            is MetricCell.Incomplete -> cell.reason == MetricIssueCode.SOURCE_STALE
            is MetricCell.Abstained -> cell.reason == MetricIssueCode.SOURCE_STALE
            is MetricCell.NotApplicable -> false
            is MetricCell.Stale -> true
            is MetricCell.Available -> false
        }

    private companion object {
        const val DATA_FRESHNESS_ORDER = 10
        const val DATA_FRESHNESS_RULE_ID = "data_freshness_guard"
        const val HMM_RULE_ID = "hmm_risk_off_guard"
        const val MEAN_REVERSION_RULE_ID = "mean_reversion_warning"
        const val NEWS_COMPONENT = "NEGATIVE_NEWS"
        const val DISCLOSURE_COMPONENT = "DISCLOSURE"
        val HARD_READINESS_METRICS =
            setOf(
                MetricKey.CURRENT_PRICE_KRW,
                MetricKey.OWNER_POSITION_QUANTITY,
                MetricKey.PORTFOLIO_EQUITY_KRW,
                MetricKey.MARGIN_REQUIREMENT_KRW,
                MetricKey.DAILY_LOSS_RATE,
                MetricKey.MDD,
                MetricKey.ANNUALIZED_VOLATILITY,
            )
        val GENERIC_OPTIONAL_COMPONENTS =
            setOf(
                OptionalEvaluationComponent.LIGHTGBM.name,
                OptionalEvaluationComponent.BSM.name,
                OptionalEvaluationComponent.GBM.name,
            )
    }
}
