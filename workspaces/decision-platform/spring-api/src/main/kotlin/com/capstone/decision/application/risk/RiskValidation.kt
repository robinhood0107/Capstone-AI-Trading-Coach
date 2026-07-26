package com.capstone.decision.application.risk

data class RiskFieldViolation(
    val field: String,
    val reason: String,
)

class RiskValidationException(
    violations: List<RiskFieldViolation>,
) : IllegalArgumentException("Risk request validation failed.") {
    val violations: List<RiskFieldViolation> =
        violations.sortedWith(compareBy(RiskFieldViolation::field, RiskFieldViolation::reason))
}
