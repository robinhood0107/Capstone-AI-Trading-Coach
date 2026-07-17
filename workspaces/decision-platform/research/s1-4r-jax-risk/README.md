# S1.4R 고급 리스크 연구 / Advanced Risk Research

## KR: 목적과 경계

이 디렉터리는 S1.4 production 계약을 바꾸지 않고 고급 리스크 통계의 수학 계약과
NumPy/JAX CPU x64 동치성을 검증하는 **격리된 연구 프로젝트**다. Production
`app.financial_engineering`을 import하거나 재노출하지 않으며, 이 패키지의 함수도 production
스타일로 최상위에서 re-export하지 않는다.

범위는 다음 아홉 함수로 고정한다.

1. `historical_expected_shortfall`
2. `realized_variance`
3. `realized_volatility_intraday`
4. `lo_adjusted_sharpe_ratio`
5. `probabilistic_sharpe_ratio`
6. `deflated_sharpe_ratio`
7. `kupiec_unconditional_coverage_test`
8. `christoffersen_independence_test`
9. `christoffersen_conditional_coverage_test`

REST, gRPC, proto, `RiskEngine`, 팀원 workspace와 계약, provider 호출, GPU gate, autodiff,
production 교체 판단은 범위 밖이다.

## EN: Purpose and boundary

This directory is an **isolated research project** for freezing the mathematical contracts of
nine advanced risk statistics and testing NumPy/JAX CPU x64 equivalence without changing the
S1.4 production contract. It neither imports nor re-exports production
`app.financial_engineering`, and it deliberately has no production-style package-level API.

REST, gRPC, protobuf, `RiskEngine`, teammate workspaces and contracts, provider calls, a GPU
gate, autodiff, and any production replacement decision are out of scope.

## KR/EN: 동결된 수학 계약 / Frozen mathematical contracts

### Historical Expected Shortfall

입력은 부호를 바꾸지 않은 loss-space 관측치다. 내림차순 손실을
\(L_{[1]}\ge\cdots\ge L_{[n]}\), \(m=n(1-c)\), \(k=\lfloor m\rfloor\),
\(\delta=m-k\)라 두면

```text
ES = (sum(L[1:k]) + delta * L[k+1]) / m
```

로 정의한다. `m < 1`이면 최악 손실 하나가 결과다. 경계 관측치를 잘라 버리는 분위수
평균이 아니라 exact fractional finite-tail mass를 사용한다.

### Realized variance and volatility

입력은 caller가 이미 만든 한 세션의 intraday log return이다.

```text
RV   = sum(r_i ** 2)
RVOL = sqrt(RV)
```

가격 변환, 평균 제거, `ddof`, 연율화, overnight 처리는 하지 않는다.

### Lo-adjusted Sharpe

`x_t = return_t - risk_free_rate`이고 population convention을 사용한다.

```text
mean    = sum(x) / n
gamma_0 = sum((x - mean) ** 2) / n
gamma_k = sum((x[k:] - mean) * (x[:-k] - mean)) / n
rho_k   = gamma_k / gamma_0

base_SR = mean / sqrt(gamma_0)
adjustment = sqrt(
    q / (1 + 2 * sum((1 - k / q) * rho_k for k in 1..q-1))
)
result = base_SR * adjustment
```

`n > q`, `q >= 1`이어야 한다. 분산과 adjustment radicand는 strictly positive다.
`q=1`은 original-frequency Sharpe이며 임의 lag truncation이나 연율화를 하지 않는다.

### Probabilistic Sharpe Ratio

SR과 benchmark는 같은 original sampling frequency다. `kurtosis`는 excess가 아닌 Pearson
kurtosis다.

```text
radicand = 1 - skewness * SR + ((kurtosis - 1) / 4) * SR ** 2
z = ((SR - benchmark_SR) * sqrt(n - 1)) / sqrt(radicand)
PSR = Phi(z)
```

`n > 1`, positive radicand, 가능한 Pearson moment pair가 필요하다. 수학적 가능성
`kurtosis >= skewness**2 + 1`에는 다음 float64 roundoff tolerance만 허용한다.

```text
64 * eps * max(1, abs(kurtosis), abs(skewness**2 + 1))
```

### Deflated Sharpe Ratio

```text
gamma_E = 0.5772156649015329
SR_star = sqrt(sharpe_estimate_variance) * (
    (1 - gamma_E) * Phi^-1(1 - 1 / N)
    + gamma_E * Phi^-1(1 - 1 / (N * e))
)
DSR = PSR(observed_SR, benchmark_SR=SR_star, ...)
```

`N >= 2`는 임의 grid 수가 아니라 출처가 검증된 effective independent trial 수다.
`sharpe_estimate_variance`는 전체 trial Sharpe 기록의 sample variance (`ddof=1`)이며
strictly positive다. 아래 provenance는 필수 keyword-only 인자다.

```python
EffectiveTrialProvenance(
    schema_version="s1.4r-effective-trials-v1",
    method="pre_registered_independent"
    # 또는 "externally_estimated_effective_count"
    ,
    raw_trial_count=...,
    effective_trial_count=...,
    sampling_frequency=...,
    trial_registry_sha256=...,
    variance_ddof=1,
)
```

`raw_trial_count >= effective_trial_count >= 2`, 함수의 `trial_count`와 effective count의
일치, 비어 있지 않은 frequency, lowercase SHA-256, `variance_ddof=1`을 검증한다. 누락이나
불일치는 `trial_provenance_invalid`이며 non-integer effective count를 반올림하지 않는다.

### VaR exception and likelihood-ratio tests

세 backtest 모두 다음 strict exception을 사용한다.

```text
I_t = 1 if realized_loss_t > forecast_var_t else 0
```

따라서 equality는 non-exception이다. Forecast VaR는 finite하고 `>= 0`이어야 한다.

Standalone Kupiec UC는 전체 `I_1:T`를 사용한다.

```text
p = 1 - confidence
p_hat = x / T
LR_uc = 2 * (logL(p_hat; I_1:T) - logL(p; I_1:T))
p_value = erfc(sqrt(LR_uc / 2))
```

Christoffersen independence는 `t=2..T` 전이 `n_ij`를 사용한다. 두 이전상태 row total이
모두 positive여야 하며 개별 cell zero는 정상이다.

```text
LR_ind = 2 * (logL_markov - logL_independent)
p_value = erfc(sqrt(LR_ind / 2))
```

Conditional coverage는 원 논문의 first-observation-conditioned likelihood를 고정한다.
내부 UC 성분은 standalone Kupiec를 재사용하지 않고 `I_2:T`를 사용한다.

```text
LR_cc = 2 * (logL_markov - logL_cc0)
conditioned_LR_uc = 2 * (logL_independent - logL_cc0)
LR_cc = conditioned_LR_uc + LR_ind
p_value = exp(-LR_cc / 2)
```

결과에는 conditioned observation/exception 수와 두 component statistic을 모두 담는다.

### Likelihood boundary policy

`0 * log(0) = 0`은 count-aware helper로 계산하며 probability를 epsilon으로 이동하지 않는다.
LR 음수는 아래 roundoff tolerance 안에서만 zero로 정규화한다.

```text
128 * eps * max(1, abs(null_log_likelihood), abs(alternative_log_likelihood))
```

그보다 큰 음수는 `likelihood_invalid`다. P-value clamp 역시 float64 roundoff 안에서만
허용하며 정상 결과의 NaN/Inf를 금지한다.

## KR/EN: 오류와 검증 순서 / Errors and validation precedence

`ResearchValidationError`는 `str(error) == error.code`를 유지한다.

```text
research_input_invalid
research_input_too_short
aggregation_periods_invalid
moment_invalid
trial_count_invalid
trial_variance_invalid
trial_provenance_invalid
significance_invalid
forecast_shape_invalid
forecast_var_negative
insufficient_sample
likelihood_invalid
research_result_non_finite
```

Sequence 함수는 container/dtype/dimension, finite, 길이, keyword type/finite, keyword domain,
moment/likelihood 식별 가능성, 계산, finite result 순서로 검증한다. VaR 함수는 realized
container, forecast container, shape, 길이, negative forecast, confidence, significance,
transition identifiability 순서다. 이 우선순위는 NumPy와 JAX public wrapper가 공유한다.

## KR/EN: JAX 실행 계약 / JAX runtime contract

JAX/JAXLIB 0.11.0을 Python 3.12와 함께 직접 pin한다. 실행 전에 반드시:

```bash
export JAX_PLATFORMS=cpu
export JAX_ENABLE_X64=1
```

Public wrapper는 backend가 CPU인지, 모든 device가 CPU인지, x64 flag가 켜졌는지, numeric
input/output이 float64인지 확인한다. Host가 검증한 fixed-shape input만 pure kernel에
전달하고 dataclass와 Python exception은 host에서 만든다.

`jnp.empty`/`empty_like`, dynamic boolean indexing, traced-value Python conversion은 금지한다.
`aggregation_periods`만 static JIT argument다. `vmap`은 독립 path/batch 선두 축에만
사용하고 time/lag/tail/transition 축에는 사용하지 않는다. Correctness는 eager와 JIT를
모두 검증하며 모든 timing은 `block_until_ready()` 뒤 기록한다.

## KR/EN: Benchmark contract

Benchmark는 correctness와 분리된 관찰 evidence이며 performance threshold가 아니다.

- 1-D axis: `size = 32, 252, 1000, 10000, 100000`; paths/horizon은 없음
- Path axis: `paths = 100, 1000, 10000`, `horizon = 252`; size는 없음
- PSR/DSR timed region: metric-only scalar batch, unit `evaluations/s`
- RNG: fixed NumPy `PCG64`; dtype float64; Lo `q=5`
- Cold: signature마다 fresh process 20개, persistent compilation cache disabled
- Warm: 5 untimed warmups + 50 timed samples
- Quantile: NumPy `linear`
- JAX: trace/lower, compile, host/device API boundary, first execute, warm execute를 분리

Allocation cap은 RSS나 container memory limit가 아니라 다음 ledger의 512 MiB
(`536870912`) upper bound다.

```text
hostInputBytes
+ hostOutputBytes
+ numpyTemporaryBytes
+ jaxArgumentBytes
+ jaxTemporaryBytes
+ jaxOutputBytes
- jaxAliasBytes
= estimatedPeakAllocationBytes
```

Chunk size는 monotone analytical upper bound에서 결정론적 binary search로 정한다.
NumPy/JAX가 같은 chunk를 사용하며 마지막 chunk는 마지막 valid path 복제로 고정 shape를
유지하고 padding 결과를 버린다. XLA `memory_analysis()`는 가용할 때 교차검증하는 debugging
estimate일 뿐 단독 proof가 아니다. 증명할 수 없으면 chunk 1에서도 중단한다. RSS는
observational evidence만 기록한다.

Speedup은 같은 host, run, fixture, affinity, threads, execution boundary에서만 계산한다.
WSL, native Linux CI, OCI 사이의 ratio는 만들지 않는다.

## KR/EN: 실행 / Running

```bash
cd workspaces/decision-platform/research/s1-4r-jax-risk
export UV_PYTHON=3.12
export JAX_PLATFORMS=cpu
export JAX_ENABLE_X64=1

uv lock --check
uv sync --frozen --all-groups
uv run --frozen ruff check .
uv run --frozen mypy src benchmarks
uv run --frozen pytest -q
```

Benchmark와 OCI 명령은 correctness gate가 통과한 뒤 `benchmarks/run.py --help`와 두 CI
workflow에 고정한다. Raw samples와 OCI archive는 tracked report가 아니라 ignored local
output 또는 CI artifact다.

## KR/EN: Tracked evidence / 추적 증거

최종 WSL full matrix와 OCI evidence는 보고서 커밋 직전의 exact source
`60ed803fb0a327ee1dbc546920464ce5693c90a9`에서 생성했다.

- [Correctness report / 정확성 보고서](reports/correctness-report.md)
- [Benchmark report / 벤치마크 보고서](reports/benchmark-report.md) —
  file SHA-256 `3328c180304ed4a5c0807136fdbd73258a64ad228cca49ee72adf792869d2d0c`
- [Benchmark manifest / 벤치마크 manifest](reports/benchmark-manifest.json) —
  file SHA-256 `f61f314d38fe5b4a88406d0247e09d62f6e92c322d3da47b66831d6b00f8fbe0`

The tracked manifest covers 62 cases and 124 NumPy/JAX results. Its sibling raw samples,
canonical plan, fixtures, wheel, and OCI archives remain ignored run artifacts; their digests
are bound into the manifest and correctness report.

## KR/EN: Canonical evidence and limitations

`tests/fixtures/canonical/advanced_risk_v1.json`은 논문 공식, 부호, moment convention,
손계산 expected value와 invalid code를 고정한다. 인접 `.sha256`은 JSON raw bytes를
검증한다. 성공 결과는 Python `float`/`int`/`bool`이고 JSON은 `allow_nan=False`로
직렬화한다.

PSR/DSR와 likelihood-ratio p-value는 작은 표본에서 asymptotic approximation이라는 한계가
있다. Effective trial provenance는 독립성을 증명하는 장치가 아니라 계산의 입력 근거를
추적하는 최소 계약이다. 이 연구는 규제 준수, model validation 승인 또는 production
교체 판단이 아니다.

## Sources

Primary research and official runtime sources:

- Acerbi & Tasche, *On the Coherence of Expected Shortfall*:
  [DOI](https://doi.org/10.1016/S0378-4266%2802%2900283-2)
- Andersen et al., *Modeling and Forecasting Realized Volatility*:
  [DOI](https://doi.org/10.1111/1468-0262.00418)
- Lo, *The Statistics of Sharpe Ratios*:
  [CFA Institute](https://rpc.cfainstitute.org/research/financial-analysts-journal/2002/the-statistics-of-sharpe-ratios)
- Bailey & López de Prado, *The Sharpe Ratio Efficient Frontier*:
  [DOI](https://doi.org/10.21314/JOR.2012.255),
  [author PDF](https://www.davidhbailey.com/dhbpapers/sharpe-frontier.pdf)
- Bailey & López de Prado, *The Deflated Sharpe Ratio*:
  [DOI](https://doi.org/10.3905/jpm.2014.40.5.094),
  [author PDF](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
- Kupiec, *Techniques for Verifying the Accuracy of Risk Measurement Models*:
  [Federal Reserve record](https://fedinprint.org/item/fedgfe/34596/original),
  [DOI](https://doi.org/10.3905/jod.1995.407942)
- Christoffersen, *Evaluating Interval Forecasts*:
  [DOI](https://doi.org/10.2307/2527341)
- JAX 0.11.0:
  [release](https://github.com/jax-ml/jax/releases/tag/jax-v0.11.0),
  [installation](https://docs.jax.dev/en/latest/installation.html),
  [x64](https://docs.jax.dev/en/latest/default_dtypes.html),
  [JIT](https://docs.jax.dev/en/latest/_autosummary/jax.jit.html),
  [benchmarking](https://docs.jax.dev/en/latest/benchmarking.html)
