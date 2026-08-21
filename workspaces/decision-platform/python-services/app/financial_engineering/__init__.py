"""S1.4 수익률·리스크 순수 계산 코어의 공개 경계다."""

from app.financial_engineering.returns import (
    cagr,
    cumulative_return,
    log_returns,
    simple_returns,
)
from app.financial_engineering.risk_metrics import (
    annualized_volatility,
    historical_cvar,
    historical_var,
    max_drawdown,
    realized_volatility,
    sharpe_ratio,
    sortino_ratio,
)

__all__ = (
    "simple_returns",
    "log_returns",
    "cumulative_return",
    "cagr",
    "realized_volatility",
    "annualized_volatility",
    "max_drawdown",
    "sharpe_ratio",
    "sortino_ratio",
    "historical_var",
    "historical_cvar",
)
from app.financial_engineering.hmm_regime import (
    HMMArtifact,
    HMMRegimeResult,
    RegimeObservation,
    build_causal_features,
    fit_hmm_regime,
)
from app.financial_engineering.gbm_monte_carlo import (
    DeterministicStressResult,
    GBMMonteCarloReport,
    StochasticMetrics,
    deterministic_stress,
    generate_terminal_prices,
    run_gbm_monte_carlo,
)

__all__ = [
    "HMMArtifact",
    "HMMRegimeResult",
    "RegimeObservation",
    "build_causal_features",
    "fit_hmm_regime",
    "DeterministicStressResult",
    "GBMMonteCarloReport",
    "StochasticMetrics",
    "deterministic_stress",
    "generate_terminal_prices",
    "run_gbm_monte_carlo",
]
