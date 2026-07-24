package com.capstone.decision.infrastructure.decision

import com.capstone.decision.application.decision.DecisionValidityPolicy
import com.capstone.decision.application.risk.MetricSnapshotAssembler
import com.capstone.decision.application.risk.PortfolioEvaluationUseCase
import com.capstone.decision.application.risk.SystemRuleContract
import com.capstone.decision.application.risk.port.DisclosureRiskPort
import com.capstone.decision.application.risk.port.InstrumentCatalogPort
import com.capstone.decision.application.risk.port.MarginPort
import com.capstone.decision.application.risk.port.NewsEvidencePort
import com.capstone.decision.application.risk.port.OrderMetricPort
import com.capstone.decision.application.risk.port.PortfolioContextPort
import com.capstone.decision.application.risk.port.PricePort
import com.capstone.decision.application.risk.port.PrincipleSnapshotPort
import com.capstone.decision.application.risk.port.RiskMetricBundle
import com.capstone.decision.application.risk.port.RiskSnapshotPort
import com.capstone.decision.application.risk.port.SignalMetricBundle
import com.capstone.decision.application.risk.port.SignalPort
import com.capstone.decision.domain.risk.MetricCell
import com.capstone.decision.domain.risk.MetricIssueCode
import com.capstone.decision.infrastructure.risk.JdbcInternalPaperBalanceAdapter
import com.capstone.decision.infrastructure.risk.JdbcKisMockBalanceAdapter
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import java.time.Duration

/**
 * S2.3이 소유하지 않는 producer가 없는 source는 production 값을 합성하지 않고 typed missing으로 닫는다.
 */
@Configuration(proxyBeanMethods = false)
class DecisionRuntimeConfiguration {
    @Bean
    fun decisionValidityPolicy(properties: DecisionProperties): DecisionValidityPolicy =
        DecisionValidityPolicy(Duration.ofMinutes(properties.validMinutes))

    @Bean
    fun decisionOrderMetricPort(): OrderMetricPort =
        object : OrderMetricPort {
            override fun loadDailyOrderCount(request: com.capstone.decision.application.risk.port.EvaluationSourceRequest) =
                MetricCell.Missing(MetricIssueCode.SOURCE_MISSING)
        }

    @Bean
    fun decisionRiskSnapshotPort(): RiskSnapshotPort =
        object : RiskSnapshotPort {
            override fun load(request: com.capstone.decision.application.risk.port.EvaluationSourceRequest) =
                RiskMetricBundle(
                    dailyLossRate = MetricCell.Missing(MetricIssueCode.SOURCE_MISSING),
                    maxDrawdown = MetricCell.Missing(MetricIssueCode.SOURCE_MISSING),
                    annualizedVolatility = MetricCell.Missing(MetricIssueCode.SOURCE_MISSING),
                )
        }

    @Bean
    fun decisionInstrumentCatalogPort(): InstrumentCatalogPort =
        object : InstrumentCatalogPort {
            override fun load(request: com.capstone.decision.application.risk.port.EvaluationSourceRequest) =
                MetricCell.Missing(MetricIssueCode.SOURCE_MISSING)
        }

    @Bean
    fun decisionNewsEvidencePort(): NewsEvidencePort =
        object : NewsEvidencePort {
            override fun loadNegativeScore(request: com.capstone.decision.application.risk.port.EvaluationSourceRequest) =
                MetricCell.Missing(MetricIssueCode.SOURCE_MISSING)
        }

    @Bean
    fun decisionSignalPort(): SignalPort =
        object : SignalPort {
            override fun load(request: com.capstone.decision.application.risk.port.EvaluationSourceRequest) =
                SignalMetricBundle(
                    hmmRiskOffProbability = MetricCell.Missing(MetricIssueCode.SOURCE_MISSING),
                    meanReversionAbsoluteZScore = MetricCell.Missing(MetricIssueCode.SOURCE_MISSING),
                )
        }

    @Bean
    fun decisionMetricSnapshotAssembler(
        pricePort: PricePort,
        kisMockBalanceAdapter: JdbcKisMockBalanceAdapter,
        internalPaperBalanceAdapter: JdbcInternalPaperBalanceAdapter,
        marginPort: MarginPort,
        orderMetricPort: OrderMetricPort,
        riskSnapshotPort: RiskSnapshotPort,
        instrumentCatalogPort: InstrumentCatalogPort,
        newsEvidencePort: NewsEvidencePort,
        disclosureRiskPort: DisclosureRiskPort,
        signalPort: SignalPort,
    ): MetricSnapshotAssembler =
        MetricSnapshotAssembler(
            pricePort = pricePort,
            kisMockBalancePort = kisMockBalanceAdapter,
            internalPaperBalancePort = internalPaperBalanceAdapter,
            marginPort = marginPort,
            orderMetricPort = orderMetricPort,
            riskSnapshotPort = riskSnapshotPort,
            instrumentCatalogPort = instrumentCatalogPort,
            newsEvidencePort = newsEvidencePort,
            disclosureRiskPort = disclosureRiskPort,
            signalPort = signalPort,
        )

    @Bean
    fun decisionPortfolioEvaluationUseCase(
        principleSnapshotPort: PrincipleSnapshotPort,
        portfolioContextPort: PortfolioContextPort,
        snapshotAssembler: MetricSnapshotAssembler,
        systemRuleContract: SystemRuleContract,
    ): PortfolioEvaluationUseCase =
        PortfolioEvaluationUseCase(
            principleSnapshotPort = principleSnapshotPort,
            portfolioContextPort = portfolioContextPort,
            snapshotAssembler = snapshotAssembler,
            systemRuleContract = systemRuleContract,
        )
}
