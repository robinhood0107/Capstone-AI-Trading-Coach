# S1.4R correctness report / 정확성 보고서

## KR: 결론

S1.4 production 계약을 변경하지 않은 격리 연구 경계에서 고급 리스크 통계 9개의
NumPy reference와 JAX CPU/x64 eager/JIT 결과가 동결된 canonical fixture 및 전체 property
matrix를 통과했다. WSL2에서 263개 테스트가 통과했고, network-disabled OCI에서는
production checkout을 의도적으로 제외해 host-only 8개를 skip한 상태로 255개가
통과했다. 동일한 263개 테스트는 native Linux GitHub Actions에서도 통과했다.

이 결과는 연구 구현의 수학 계약과 재현성 evidence다. 규제 검증, model validation 승인,
성능 합격선 또는 production 구현 교체 결론이 아니다.

## EN: Conclusion

Within an isolated research boundary that does not change the S1.4 production contract, the
NumPy references and JAX CPU/x64 eager/JIT implementations for all nine advanced-risk
statistics passed the frozen canonical fixtures and the complete property matrix. The WSL2
run passed 263 tests. The network-disabled OCI run passed 255 tests with eight intentional
host-only skips because the research image excludes the production checkout. The same 263
tests also passed in native Linux GitHub Actions.

This is mathematical-contract and reproducibility evidence for a research implementation. It
is not regulatory validation, model-validation approval, a performance threshold, or a
production replacement decision.

## Evidence identity / 증거 식별자

- Source commit measured before this report commit:
  `60ed803fb0a327ee1dbc546920464ce5693c90a9`
- Canonical fixture:
  `tests/fixtures/canonical/advanced_risk_v1.json`
- Canonical fixture raw SHA-256:
  `298bb93d8915d2702d32d298b6acab7fd0c2dc881f0707cb6e585fb78d420891`
- Full benchmark run ID: `run-20260717T155054Z-030da729`
- Full benchmark manifest file SHA-256:
  `f61f314d38fe5b4a88406d0247e09d62f6e92c322d3da47b66831d6b00f8fbe0`
- Canonical manifest-object SHA-256:
  `a4ca7978355852f5dfc0e61730cec67ba7be486c0b3b5e0711bc363b6ab117d5`
- Raw-samples canonical-object SHA-256:
  `5592db44c80402d83750dda99840106762427ae3797d77174a03c54fa1531c11`
- Raw-samples file SHA-256:
  `c4bd2fafcf18f1f696903b5bca1376fb6657b6f6c670a0efbd3280f9ecd5319e`

The object digest is over strict JSON with sorted keys and no trailing newline. The file
digest additionally covers the tracked/generated trailing newline; both are recorded to avoid
mixing the two serialization boundaries.

## Frozen contracts and representative results / 동결 계약과 대표 결과

Paper/hand fixtures have the acceptance ceiling `rtol=1e-12`, `atol=1e-12`; their direct
NumPy assertions deliberately tighten this to zero relative tolerance and `atol=1e-15` where
the expected decimal is not exactly representable. Large/property and NumPy/JAX scalar parity
checks use `rtol=1e-10`, `atol=1e-12`. Integer, boolean, transition-count, error-code, dtype,
backend, and shape fields are exact.

The frozen equations use loss observations \(L\), intraday log returns \(r_i\), excess returns
\(x_t\), exception indicators \(I_t=1[\text{loss}_t>\text{VaR}_t]\), the standard-normal CDF
\(\Phi\), and Euler's constant \(\gamma_E\):

```text
m = n(1-c), k = floor(m), delta = m-k
ES = (sum(L_[1:k]) + delta * L_[k+1]) / m
     where losses are descending and m < 1 returns the single worst loss

RV = sum(r_i^2)
RVOL = sqrt(RV)

gamma_0 = sum((x-mean(x))^2) / n
gamma_k = sum((x[k:]-mean(x)) * (x[:-k]-mean(x))) / n
rho_k = gamma_k / gamma_0
Lo_SR = (mean(x) / sqrt(gamma_0))
        * sqrt(q / (1 + 2 * sum((1-k/q) * rho_k, k=1..q-1)))

PSR = Phi((SR-benchmark_SR) * sqrt(n-1)
          / sqrt(1-skewness*SR+((kurtosis-1)/4)*SR^2))

SR_star = sqrt(sharpe_estimate_variance)
          * ((1-gamma_E)*Phi^-1(1-1/N) + gamma_E*Phi^-1(1-1/(N*e)))
DSR = PSR(observed_SR, benchmark_SR=SR_star, ...)

LR_uc = 2 * (logL(p_hat; I_1:T) - logL(1-confidence; I_1:T))
LR_ind = 2 * (logL_markov(I_2:T) - logL_independent(I_2:T))
conditioned_LR_uc =
    2 * (logL_independent(I_2:T) - logL_cc0(I_2:T))
LR_cc = conditioned_LR_uc + LR_ind
```

Standalone Kupiec therefore uses the full sequence, while both Christoffersen components are
conditioned on the first observation. Kupiec and independence use the one-degree-of-freedom
chi-square survival function; conditional coverage uses the two-degree-of-freedom survival
function `exp(-LR_cc/2)`. Zero-count likelihood terms follow `0*log(0)=0` without moving
probabilities by epsilon.

| Function | Primary source and frozen convention | Representative fixture | NumPy | JAX eager | JAX JIT |
|---|---|---|---:|---:|---:|
| `historical_expected_shortfall` | Acerbi–Tasche; loss space, descending exact fractional finite-tail mass | losses `[1,2,3,4]`, confidence `0.625` | `3.6666666666666665` | `3.6666666666666665` | `3.6666666666666665` |
| `realized_variance` | Andersen et al.; one-session intraday log-return square sum, no centering/annualization | `[1,-2,2]` | `9.0` | `9.0` | `9.0` |
| `realized_volatility_intraday` | Non-negative square root of the frozen realized variance | `[1,-2,2]` | `3.0` | `3.0` | `3.0` |
| `lo_adjusted_sharpe_ratio` | Lo; population moments, original frequency, positive autocorrelation adjustment denominator | returns `[-1,0,1,2]`, `q=2` | `0.565685424949238` | `0.565685424949238` | `0.565685424949238` |
| `probabilistic_sharpe_ratio` | Bailey–López de Prado; original-frequency SR, Pearson kurtosis, `sqrt(n-1)` | SR `1`, benchmark `0`, `n=6`, skew `0`, kurtosis `3` | `0.9660554225690855` | `0.9660554225690855` | `0.9660554225690855` |
| `deflated_sharpe_ratio` | Bailey–López de Prado; effective independent trial provenance and `ddof=1` trial-SR variance required | selected SR `1`, effective trials `2`, variance `1` | `0.8097031129023626` | `0.8097031129023626` | `0.8097031129023626` |
| `kupiec_unconditional_coverage_test` | Kupiec; full sequence, strict `loss > VaR`, `0*log(0)=0` | 4 observations, 0 exceptions, confidence `0.75` | LR `2.301456579614247`, p `0.1292527395940427` | same | same |
| `christoffersen_independence_test` | Christoffersen; `t=2..T` transition likelihood, identifiable rows | transitions `(0,2,2,0)` | LR `5.545177444479562`, p `0.018531677751199068` | same | same |
| `christoffersen_conditional_coverage_test` | Christoffersen first-observation-conditioned null; conditioned UC plus independence identity | 4 conditioned observations, 2 exceptions | LR `5.708465422560582`, p `0.05760000000000002` | same | same |

The conditional-coverage representative also matched both components exactly:
conditioned UC `0.16328797808102014` and independence `5.545177444479562`.
For all three backtests, the timed raw JAX likelihood fields—not only the public statistic and
p-value—were compared over every valid generated path, including non-divisible padded chunks.

## DSR provenance / DSR 출처

The benchmark DSR cases use a serialized two-trial daily registry rather than a dummy grid
count:

```text
schemaVersion: s1.4r-effective-trials-v1
method: pre_registered_independent
rawTrialCount: 2
effectiveTrialCount: 2
samplingFrequency: daily
varianceDdof: 1
sharpeEstimateVariance: 0.04
trialRegistrySha256: a40fd68290a4dfadabc80e16e9adba4226e8a470e336774afff548829825e706
registrySerialization: strict-json-sort-keys-utf8-v1
```

Missing, malformed, frequency/count-mismatched, or digest-invalid provenance fails before DSR
calculation with `trial_provenance_invalid`. PSR/DSR inputs are neither annualized nor replaced
with Lo-adjusted Sharpe values.

## Stable error matrix / 안정 오류 행렬

NumPy and JAX eager/JIT share the exact string contract below. Composite-invalid tests freeze
validation precedence so a later-domain error cannot hide an earlier container/type/finite
error.

| Stable code | Frozen failure class |
|---|---|
| `research_input_invalid` | invalid container, dimension, dtype, non-finite value, or scalar keyword type |
| `research_input_too_short` | sequence cannot satisfy the function’s minimum length |
| `aggregation_periods_invalid` | Lo aggregation period is non-integer or outside its domain |
| `moment_invalid` | zero/invalid variance, impossible Pearson moments, or non-positive PSR/Lo radicand |
| `trial_count_invalid` | DSR effective trial count is not an exact integer `N >= 2` |
| `trial_variance_invalid` | DSR trial Sharpe sample variance is non-finite or non-positive |
| `trial_provenance_invalid` | effective-trial provenance is absent, malformed, or inconsistent |
| `significance_invalid` | test significance is not finite and strictly between zero and one |
| `forecast_shape_invalid` | realized-loss and forecast-VaR shapes differ |
| `forecast_var_negative` | forecast VaR contains a negative value |
| `insufficient_sample` | transition likelihood rows are not identifiable |
| `likelihood_invalid` | LR/log-likelihood identity violates the frozen roundoff policy |
| `research_result_non_finite` | a legal computation cannot produce a finite float64 result |

Boundary regressions include subnormal confidence, `N=10**308`, maximum finite float64 constant
ES samples, zero likelihood cells, and ill-conditioned but positive Lo denominators.
The `N=10**308` DSR regression directly exercises the eager and JIT JAX DSR kernel, including
its log-tail inverse-normal, trial-count, and variance arithmetic rather than substituting a
host-computed benchmark Sharpe value.

## Runtime evidence / 실행 증거

| Boundary | Result | Runtime contract |
|---|---|---|
| WSL2 local | `263 passed` | Python `3.12.13`, NumPy `2.5.1`, JAX/JAXLIB `0.11.0`, CPU backend/device, x64 enabled |
| OCI, network disabled | `255 passed, 8 skipped` | Digest-pinned base images; research package/JAX present; production `app` absent; eight production-checkout-dependent tests intentionally host-only |
| Native Linux GitHub Actions | [`263 passed` — success](https://github.com/robinhood0107/Capstone-AI-Trading-Coach/actions/runs/29596019527/job/87936479600) | `Native Linux CPU x64 correctness` ran lock, production/research sync, runtime assertion, Ruff, mypy, and all tests |

WSL environment evidence:

```text
WSL 2.7.10.0
WSL kernel version 6.18.33.2-2
Linux release 6.18.33.2-microsoft-standard-WSL2
architecture x86_64
backend cpu
device cpu:0
x64 true
thread counts OMP/OpenBLAS/MKL/NUMEXPR = 1
```

OCI identity evidence:

```text
image/manifest:
sha256:a2472f838329657209f4be4f4e44bc4ee2aac44532e82ad051ad2019d1789472
single-build verification:
docker_descriptor_matches_oci_manifest
network:
none
```

The native-Linux row is deliberately not reported as passed before the PR check reaches a
terminal state. The PR check is the authoritative native-Linux result.

## Production isolation / Production 격리

The WSL host tests verified:

- the branch diff is confined to this research project and its two workflows;
- production `app.financial_engineering.__all__` remains the exact original 11-function
  surface;
- production source and lock contain no JAX/JAXLIB/research dependency;
- the production virtual environment cannot resolve JAX, JAXLIB, or this research package;
- the research environment cannot resolve the production `app` package;
- the four frozen production/reference files preserve both Git blob ID and SHA-256;
- production workspaces, contracts, API documents, and teammate placeholders have no branch
  diff;
- local private notes are ignored, untracked, and unstaged.

The OCI image copies only the isolated research source, tests, benchmark controller, lock, and
README. It excludes the production checkout by construction and runs its correctness suite
with network access disabled.

## Limitations / 한계

- PSR, DSR, and likelihood-ratio p-values are asymptotic approximations, especially for small
  samples.
- Effective-trial provenance makes the supplied count auditable; it does not itself prove
  statistical independence.
- Canonical parity and property tests cannot establish model suitability for a new business
  domain.
- OCI host-only skips do not replace the WSL/native-Linux production-isolation checks.
- Benchmark timings are observational and host/run specific; they are not correctness proof.
- No GPU, autodiff, REST/gRPC/protobuf surface, provider call, or native executable is in
  scope.
