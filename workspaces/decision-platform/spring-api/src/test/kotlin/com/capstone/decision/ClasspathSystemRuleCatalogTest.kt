package com.capstone.decision

import com.capstone.decision.application.risk.CatalogRuleOwnership
import com.capstone.decision.domain.risk.MetricKey
import com.capstone.decision.domain.risk.PortfolioSource
import com.capstone.decision.infrastructure.risk.ClasspathSystemRuleCatalog
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import tools.jackson.databind.json.JsonMapper

class ClasspathSystemRuleCatalogTest {
    private val catalog = ClasspathSystemRuleCatalog(JsonMapper.builder().build())

    @Test
    fun `canonical catalog fixes all fourteen dispositions and numeric version`() {
        assertThat(catalog.catalogVersion).isEqualTo(1)
        assertThat(catalog.readinessPolicyVersion).isEqualTo("s2-2-readiness-v1")
        assertThat(catalog.rules).hasSize(14)
        assertThat(catalog.rules.count { it.ownership == CatalogRuleOwnership.PUBLIC_PRINCIPLE }).isEqualTo(8)
        assertThat(catalog.rules.count { it.ownership == CatalogRuleOwnership.SYSTEM_MANAGED }).isEqualTo(6)
        assertThat(catalog.rules.count { it.executionKind == "THRESHOLD" }).isEqualTo(12)
        assertThat(catalog.rules.count { it.executionKind == "READINESS" }).isEqualTo(1)
        assertThat(catalog.rules.count { it.executionKind == "NOT_APPLICABLE" }).isEqualTo(1)
        assertThat(MetricKey.fromWire("mean_reversion_abs_z_score").unit.name).isEqualTo("ABS_Z_SCORE")
    }

    @Test
    fun `portfolio storage mapping is explicit and has no inferred fallback`() {
        assertThat(catalog.storageSource(PortfolioSource.KIS_MOCK)).isEqualTo("KIS_MOCK")
        assertThat(catalog.storageSource(PortfolioSource.INTERNAL_PAPER)).isEqualTo("PAPER")
    }
}
