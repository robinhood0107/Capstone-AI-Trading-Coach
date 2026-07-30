package com.capstone.decision.domain.risk

import java.math.BigDecimal
import java.math.BigInteger
import java.math.MathContext

enum class MetricUnit {
    KRW,
    COUNT,
    RATIO,
    SCORE,
    ABS_Z_SCORE,
    QUANTITY,
}

// metric key와 단위를 한 타입에 묶어 catalog/operator가 서로 다른 단위를 조용히 비교하지 못하게 한다.
enum class MetricKey(
    val wireName: String,
    val unit: MetricUnit,
) {
    ORDER_AMOUNT_KRW("order_amount_krw", MetricUnit.KRW),
    ASSET_WEIGHT("asset_weight", MetricUnit.RATIO),
    GOLD_ETF_ETN_WEIGHT("gold_etf_etn_weight", MetricUnit.RATIO),
    DAILY_LOSS_RATE("daily_loss_rate", MetricUnit.RATIO),
    MDD("mdd", MetricUnit.RATIO),
    DAILY_ORDER_COUNT("daily_order_count", MetricUnit.COUNT),
    NEGATIVE_NEWS_SCORE("negative_news_score", MetricUnit.RATIO),
    DISCLOSURE_RISK_SCORE("disclosure_risk_score", MetricUnit.RATIO),
    ANNUALIZED_VOLATILITY("annualized_volatility", MetricUnit.RATIO),
    HMM_RISK_OFF_PROBABILITY("hmm_risk_off_probability", MetricUnit.RATIO),
    MEAN_REVERSION_Z_SCORE("mean_reversion_abs_z_score", MetricUnit.ABS_Z_SCORE),
    ETF_ETN_RISK_SCORE("etf_etn_product_risk_score", MetricUnit.RATIO),
    CURRENT_PRICE_KRW("current_price_krw", MetricUnit.KRW),
    OWNER_POSITION_QUANTITY("owner_position_quantity", MetricUnit.QUANTITY),
    PORTFOLIO_EQUITY_KRW("portfolio_equity_krw", MetricUnit.KRW),
    MARGIN_REQUIREMENT_KRW("margin_requirement_krw", MetricUnit.KRW),
    ;

    companion object {
        fun fromWire(value: String): MetricKey =
            entries.singleOrNull { it.wireName == value }
                ?: throw IllegalArgumentException("Unknown metric key.")
    }
}

enum class MetricSource {
    KIS_MOCK,
    INTERNAL_PAPER,
    RISK_SNAPSHOT,
    NEWS,
    OPENDART,
    SIGNAL,
    INSTRUMENT_CATALOG,
    INTERNAL,
}

enum class PortfolioSource {
    KIS_MOCK,
    INTERNAL_PAPER,
    ;

    companion object {
        fun parse(value: String): PortfolioSource =
            entries.singleOrNull { it.name == value }
                ?: throw InvalidPortfolioSourceException()
    }
}

class InvalidPortfolioSourceException : IllegalArgumentException("Invalid portfolio source.")

enum class MetricIssueCode {
    SOURCE_MISSING,
    SOURCE_STALE,
    SOURCE_ERROR,
    SOURCE_INCOMPLETE,
    SOURCE_OVERSIZED,
    SOURCE_FUTURE_TIMESTAMP,
    MODEL_ABSTAINED,
    NOT_APPLICABLE,
    PORTFOLIO_CONTEXT_UNAVAILABLE,
    BROKERAGE_UNAVAILABLE,
    PAPER_PORTFOLIO_UNAVAILABLE,
    DISCLOSURE_UNAVAILABLE,
}

// 정수 금융 값은 Long으로, 비율은 선언 scale을 가진 BigDecimal로 보존해 double 반올림을 배제한다.
sealed interface MetricValue {
    val unit: MetricUnit
    val declaredScale: Int

    fun asBigDecimal(): BigDecimal

    fun compareTo(threshold: BigDecimal): Int = asBigDecimal().compareTo(threshold)

    data class Whole(
        val value: Long,
        override val unit: MetricUnit,
    ) : MetricValue {
        override val declaredScale: Int = 0

        init {
            require(unit == MetricUnit.KRW || unit == MetricUnit.COUNT || unit == MetricUnit.QUANTITY) {
                "Whole metric must use an integer unit."
            }
        }

        override fun asBigDecimal(): BigDecimal = BigDecimal.valueOf(value)
    }

    data class Decimal(
        val value: BigDecimal,
        override val declaredScale: Int,
        override val unit: MetricUnit,
    ) : MetricValue {
        init {
            require(declaredScale in 0..18) { "Metric scale is out of range." }
            require(value.stripTrailingZeros().scale().coerceAtLeast(0) <= declaredScale) {
                "Metric value exceeds its declared scale."
            }
            require(unit == MetricUnit.RATIO || unit == MetricUnit.SCORE || unit == MetricUnit.ABS_Z_SCORE) {
                "Decimal metric must use a decimal unit."
            }
        }

        override fun asBigDecimal(): BigDecimal = value
    }

    // portfolio 비율은 threshold 비교까지 분수를 유지해 scale-4 선반올림으로 violation이 PASS가 되지 않게 한다.
    data class RatioFraction(
        val numerator: Long,
        val denominator: Long,
        override val declaredScale: Int = 4,
    ) : MetricValue {
        override val unit: MetricUnit = MetricUnit.RATIO

        init {
            require(numerator >= 0)
            require(denominator > 0)
            require(declaredScale in 0..18)
        }

        override fun asBigDecimal(): BigDecimal = BigDecimal(numerator).divide(BigDecimal(denominator), MathContext.DECIMAL128)

        override fun compareTo(threshold: BigDecimal): Int {
            val thresholdScale = threshold.scale()
            val numeratorInteger = BigInteger.valueOf(numerator)
            val denominatorInteger = BigInteger.valueOf(denominator)
            val thresholdInteger = threshold.unscaledValue()
            return if (thresholdScale >= 0) {
                numeratorInteger
                    .multiply(BigInteger.TEN.pow(thresholdScale))
                    .compareTo(denominatorInteger.multiply(thresholdInteger))
            } else {
                numeratorInteger.compareTo(
                    denominatorInteger
                        .multiply(thresholdInteger)
                        .multiply(BigInteger.TEN.pow(-thresholdScale)),
                )
            }
        }
    }
}
