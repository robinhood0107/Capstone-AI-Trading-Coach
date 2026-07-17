# 수익률·리스크 지표 정의

## 1. 목적과 권위

이 문서는 S1.4에서 구현하는 수익률·리스크 순수 계산 코어의 팀 공통 단일 진실 소스다.
공식, 입력과 출력, 부호, 주기, estimator, 오류, 수치 정책을 고정한다.

문서 권위는 `docs/최종_프로젝트_명세서.md`, `docs/API_명세서.md`, 이 문서,
`contracts/` 순이다. 상위 두 공개 명세와 충돌하면 상위 명세가 우선한다. `contracts/`의
wire field는 이 문서의 계산 결과를 소비할 수 있지만 계산 공식을 다시 정의하지 않는다.

S1.4는 과거 관측값의 기술통계를 계산한다. 예측 모델, 공정가치 모형, 거래 신호,
RiskEngine 또는 주문 승인기가 아니다. 계산이 재현된다는 사실만으로 통계적·경제적
타당성이나 투자 성과를 보장하지 않는다.

## 2. 입력·출력·부호·주기 convention

### 2.1 런타임 입력

허용하는 top-level 값은 다음 exact 타입뿐이다.

- `type(values) is list`
- `type(values) is tuple`
- `type(values) is numpy.ndarray`

`list`와 `tuple`의 원소는 bool을 제외한 exact built-in `int` 또는 `float`만 허용한다.
exact base `ndarray`는 1차원 integer 또는 floating dtype만 허용한다. ndarray 안의 NumPy
integer/floating 값은 허용하지만 ndarray subclass, MaskedArray, memmap, NumPy scalar
keyword는 허용하지 않는다.

public annotation은 다음 범위를 표현하되 runtime validator가 최종 권위다.

```python
NumericInput = (
    list[int | float]
    | tuple[int | float, ...]
    | npt.NDArray[np.number[Any]]
)
FloatArray = npt.NDArray[np.float64]
```

문자열, bytes, Decimal, Fraction, bool, complex, generator, iterator, nested/ragged
container, object/string/datetime dtype, 2차원 이상 배열, NaN과 무한대는 거부한다.
사용자 정의 list/tuple/int/float subclass도 거부한다. 입력 길이 상한은 100,000이다.
top-level `np.bool_`는 bool stable error로 거부한다. list/tuple 안의 NumPy bool/complex
scalar는 각각 bool/complex stable error로 거부한다. top-level NumPy complex를 포함한 그
밖의 NumPy scalar는 허용 family가 아니므로 type error로 거부한다.

모든 public 함수는 입력을 정확히 한 번 검증하고
`np.array(values, dtype=np.float64, copy=True)`에 준하는 새 배열로 격리한다. 원본을
변경하지 않고 결과와 원본이 메모리를 공유하지 않게 한다.

### 2.2 keyword

- `periods_per_year`: exact built-in `int`, bool 제외, `> 0`
- `risk_free_rate`: exact built-in `int | float`, bool 제외, finite
- `target_return`: exact built-in `int | float`, bool 제외, finite
- `confidence`: exact built-in `int | float`, bool 제외, finite,
  `0 < confidence < 1`

### 2.3 출력과 부호

- vector 결과는 원본과 alias하지 않는 새 `float64 ndarray`다.
- scalar 결과는 Python `float`다.
- 수익률은 퍼센트가 아닌 signed decimal이다. `-0.10`은 `-10%`를 뜻한다.
- MDD는 signed decimal `[-1, 0]`이다.
- Historical VaR와 CVaR는 signed lower-tail return이다. 손실은 보통 음수다.
- downstream이 positive loss magnitude를 요구하면 경계에서 `-var95`, `-cvar95`로
  변환한다. S1.4 코어는 부호를 반전하거나 이익 관측을 0으로 clamp하지 않는다.
- 성공 결과에 NaN이나 무한대를 허용하지 않는다.

### 2.4 주기

- 기본 `periods_per_year=252`는 프로젝트의 일봉 convention이다.
- CAGR의 가격 관측은 균등 거래주기이며 간격 수는 `n - 1`이다.
- timestamp나 calendar duration을 입력에서 추측하지 않는다.
- Sharpe의 `risk_free_rate`와 Sortino의 `target_return`은 입력 return과 같은 주기다.
- adjusted/total-return 가격의 CAGR은 성과 CAGR이고 raw close의 CAGR은 price-return
  CAGR이다.

## 3. 11개 함수 signature와 수학 정의

### 3.1 `simple_returns`

```python
simple_returns(prices: NumericInput) -> FloatArray
```

```text
R_t = P_t / P_(t-1) - 1
```

가격은 모두 finite이며 `> 0`이어야 한다.

### 3.2 `log_returns`

```python
log_returns(prices: NumericInput) -> FloatArray
```

```text
r_t = log(P_t) - log(P_(t-1))
```

구현 정의는 두 가격의 로그 차이다. `log(price ratio)`로 바꾸지 않는다. 작은 변화의 독립
검산에는 `log1p(simple_return)`을 사용할 수 있지만 production 정의를 대체하지 않는다.

### 3.3 `cumulative_return`

```python
cumulative_return(returns: NumericInput) -> float
```

```text
R_cumulative = product(1 + R_t) - 1
```

단순수익률 `R_t < -1`은 거부한다. `R_t == -1`은 허용하며 정상 결과는 `-1.0`이다.

### 3.4 `cagr`

```python
cagr(
    prices: NumericInput,
    *,
    periods_per_year: int = 252,
) -> float
```

```text
CAGR = expm1(periods_per_year / (n - 1) * log(P_last / P_first))
```

중간 ratio overflow를 피하기 위해 다음 수치적으로 동등한 log 차 구현을 사용해도 된다.

```text
CAGR = expm1(periods_per_year / (n - 1)
             * (log(P_last) - log(P_first)))
```

1년 미만 구간도 계산할 수 있지만 이를 GIPS 준수 외부 보고의 연환산 성과로 표시하지 않는다.

### 3.5 `realized_volatility`

```python
realized_volatility(log_returns: NumericInput) -> float
```

```text
realized_volatility = std(log_returns, ddof=1)
```

이 호환 함수명은 daily log-return sample volatility를 뜻한다. 고빈도 intraday return의
제곱합을 쓰는 학술적 realized volatility estimator가 아니다. `ddof=1`은 `N - 1`
sample-variance convention이며 표준편차 자체를 불편추정량이라고 주장하지 않는다.

### 3.6 `annualized_volatility`

```python
annualized_volatility(
    log_returns: NumericInput,
    *,
    periods_per_year: int = 252,
) -> float
```

```text
annualized_volatility
  = std(log_returns, ddof=1) * sqrt(periods_per_year)
```

자기상관이나 volatility clustering이 있는 모든 자료에 `sqrt(252)`가 보편적으로 정확하다고
해석하지 않는다.

### 3.7 `max_drawdown`

```python
max_drawdown(equity_curve: NumericInput) -> float
```

```text
running_max_t = max(equity_0, ..., equity_t)
drawdown_t = equity_t / running_max_t - 1
MDD = min(drawdown_t)
```

첫 equity는 `> 0`, 이후 값은 `>= 0`이어야 한다. 따라서 `[100, 0]`은 허용하며 결과는
`-1.0`이다. 단일·상수·비감소 curve의 결과는 `0.0`이다.

### 3.8 `sharpe_ratio`

```python
sharpe_ratio(
    returns: NumericInput,
    *,
    risk_free_rate: int | float = 0.0,
    periods_per_year: int = 252,
) -> float
```

```text
excess_t = returns_t - risk_free_rate
Sharpe = mean(excess) / std(excess, ddof=1)
         * sqrt(periods_per_year)
```

기본 risk-free `0.0`은 프로젝트 v1 선택이다. 분모가 0이면 계산값을 꾸미지 않고
`denominator_zero`를 반환한다.

### 3.9 `sortino_ratio`

```python
sortino_ratio(
    returns: NumericInput,
    *,
    target_return: int | float = 0.0,
    periods_per_year: int = 252,
) -> float
```

```text
excess_t = returns_t - target_return
downside_t = min(excess_t, 0)
downside_deviation = sqrt(mean(downside_t ** 2))
Sortino = mean(excess) / downside_deviation
          * sqrt(periods_per_year)
```

target 또는 MAR은 risk-free와 다른 개념이다. 분모는 전체 `n`개 관측을 사용하며 목표 이상
관측에는 0을 넣는다. 하방 관측 개수만으로 나누는 estimator를 사용하지 않는다. 분모가
0이면 `denominator_zero`다.

### 3.10 `historical_var`

```python
historical_var(
    returns: NumericInput,
    *,
    confidence: int | float = 0.95,
) -> float
```

```text
Historical VaR
  = quantile(returns, 1 - confidence, method="linear")
```

NumPy `linear`, 즉 Hyndman–Fan type 7은 v1 호환 선택이다. Hyndman–Fan 논문이 type 7을
추천했다고 주장하지 않는다.

### 3.11 `historical_cvar`

```python
historical_cvar(
    returns: NumericInput,
    *,
    confidence: int | float = 0.95,
) -> float
```

```text
var = quantile(returns, 1 - confidence, method="linear")
Historical CVaR = mean(returns[returns <= var])
```

반드시 VaR threshold 이하의 실제 관측을 모두 평균한다. 경계 동률은 nominal tail mass를
늘릴 수 있다. 이 함수는 `v1 threshold-tail historical CVaR`이며 fractional boundary
weighting을 쓰는 exact finite-sample Expected Shortfall과 동일하지 않다.

## 4. 최소 길이

| 함수 | 최소 입력 길이 |
|---|---:|
| `simple_returns` | 가격 2 |
| `log_returns` | 가격 2 |
| `cumulative_return` | return 1 |
| `cagr` | 가격 2 |
| `realized_volatility` | log return 2 |
| `annualized_volatility` | log return 2 |
| `max_drawdown` | equity 1 |
| `sharpe_ratio` | return 2 |
| `sortino_ratio` | return 2 |
| `historical_var` | return 2 |
| `historical_cvar` | return 2 |

## 5. validation과 19개 오류

### 5.1 canonical precedence

복합 오류에서도 다음 순서의 첫 오류만 반환한다.

1. top-level built-in bool 또는 `np.bool_` → `input_bool_invalid`
2. 허용 top-level exact family가 아님 → `input_type_invalid`
3. exact base ndarray:
   1. `ndim != 1` → `input_shape_invalid`
   2. bool dtype → `input_bool_invalid`
   3. complex dtype → `input_complex_invalid`
   4. integer/floating 외 dtype → `input_type_invalid`
4. exact list/tuple:
   1. nested/ragged/container element → `input_shape_invalid`
   2. built-in bool 또는 `np.bool_` element → `input_bool_invalid`
   3. built-in complex 또는 NumPy complex scalar element → `input_complex_invalid`
   4. exact built-in int/float가 아닌 element → `input_type_invalid`
5. copy 전 길이:
   1. 0 → `input_empty`
   2. 100,001 이상 → `input_too_long`
   3. 함수별 최소 길이 미달 → `input_too_short`
6. 새 bounded `float64` copy 생성
   - conversion `OverflowError` → `input_non_finite`
7. copy의 NaN/무한대 → `input_non_finite`
8. keyword type/range를 signature 순서로 검증
9. 가격/equity/simple-return domain 검증
10. kernel 실행
11. 결과 finite 재검증

top-level `np.bool_`와 list/tuple 안의 `np.bool_`는 semantic bool 오류인
`input_bool_invalid`로 정규화한다. 2차원 bool ndarray는 dtype보다 shape를 먼저 검사하므로
`input_shape_invalid`다. public Python signature 자체를 어긴 호출의 argument-binding
`TypeError`는 아래 19개 runtime validation 오류와 별개다.

### 5.2 stable errors

모든 public 오류는 정확히 `ValueError("<code>")`다.

| code | 의미 |
|---|---|
| `input_type_invalid` | 허용하지 않은 family, element 또는 dtype |
| `input_shape_invalid` | 다차원 또는 nested/ragged 입력 |
| `input_empty` | 길이 0 |
| `input_too_short` | 함수별 최소 길이 미달 |
| `input_too_long` | 길이 100,001 이상 |
| `input_bool_invalid` | bool/NumPy bool scalar·element 또는 1차원 bool dtype |
| `input_complex_invalid` | built-in/NumPy complex element 또는 complex dtype |
| `input_non_finite` | 입력 NaN/무한대 또는 float64 변환 overflow |
| `prices_non_positive` | 가격 중 하나 이상이 0 이하 |
| `equity_initial_non_positive` | 첫 equity가 0 이하 |
| `equity_negative` | 첫 값 이후 equity가 음수 |
| `simple_return_below_minus_one` | 단순수익률이 `-1` 미만 |
| `periods_per_year_invalid` | exact 양의 built-in int가 아님 |
| `risk_free_rate_invalid` | exact built-in 숫자·finite 조건 위반 |
| `target_return_invalid` | exact built-in 숫자·finite 조건 위반 |
| `confidence_invalid` | exact built-in 숫자·finite·open interval 조건 위반 |
| `denominator_zero` | Sharpe 또는 Sortino 분모가 0 |
| `tail_empty` | threshold-tail 방어 분기에서 tail 관측이 없음 |
| `result_non_finite` | kernel 오류 또는 NaN/무한대 결과 |

validated finite input의 kernel에서 발생한 `FloatingPointError` 또는 `OverflowError`는
`result_non_finite`로 정규화한다. raw NumPy/Python numeric exception을 외부에 노출하지
않는다. `tail_empty`는 정상 linear VaR 경로에서는 사실상 도달하지 않지만 private
threshold-tail helper의 방어 계약으로 유지한다.

## 6. 순수성·수치 정책

모든 public 함수는 이름 있는 `def`, type hint, 한글 docstring을 사용하며 이 문서를
계약으로 인용한다.

다음을 금지한다.

- 파일, network, DB, Redis, Kafka, WebSocket
- 환경변수, clock, random
- mutable global state, cache, logging
- in-place 연산, NumPy `out=`, `overwrite_input=True`
- 전역 `np.seterr`
- 계산 코어 전체의 lambda
- 계산 코어 전체의 generator expression, `yield`, `yield from`
- pandas/SciPy 계산 위임

public 함수끼리 호출해 같은 입력을 재검증하거나 중복 copy하지 않는다. 공통 internal
kernel은 이미 검증된 새 `float64` 배열만 받는다. 수치 kernel은 다음 지역 정책을 사용한다.

```python
np.errstate(
    over="raise",
    divide="raise",
    invalid="raise",
    under="ignore",
)
```

underflow 뒤 finite float64 결과가 남으면 허용한다. 이는 float64 표현 한계다. exact
`r == -1` 때문에 누적 product가 0이 되는 것은 정상이다. overflow/divide/invalid 또는
성공 경로의 NaN/무한대는 `result_non_finite`다.

순수성은 부작용이 없고 같은 입력에 같은 결과가 나오는 성질이다. 참조 투명성은 호출식을
결과로 치환할 수 있는 성질이고, 결정성은 같은 pinned 환경과 입력에 같은 결과가 나오는
성질이다. 이들을 `f(f(x)) = f(x)`라는 대수적 멱등성과 혼동하지 않는다.

## 7. 6개 손계산 fixture

독립 oracle은 production NumPy 계산을 그대로 되풀이하지 않고 손계산, `math`, `decimal`,
`fractions`를 사용한다. finite 실수 기본 허용오차는 `rtol=1e-12`, `atol=1e-12`이고 stable
error는 exact string으로 비교한다.

| fixture | 입력 | 기대 결과 |
|---|---|---|
| Constant | prices `[100, 100, 100]`, returns `[0, 0]`, equity `[100, 100, 100]` | simple/log `[0,0]`, cumulative/CAGR/volatility/MDD/VaR/CVaR `0`; Sharpe/Sortino는 `denominator_zero` |
| Compounding | prices `[100, 110, 99]`, `periods_per_year=2` | simple `[0.1,-0.1]`, log `[log(1.1),log(0.9)]`, cumulative `-0.01`, CAGR `-0.01` |
| Volatility | log returns `[0,0.1,-0.1]` | sample volatility `0.1`, `periods_per_year=4` annualized volatility `0.2` |
| Drawdown | equity `[100,120,90,108,60]` | drawdowns `[0,0,-0.25,-0.1,-0.5]`, MDD `-0.5` |
| Tail | returns `[-0.10,-0.05,0,0.05,0.10]`, confidence `0.8` | linear VaR `-0.06`, threshold CVaR `-0.10` |
| Ratios | returns `[-0.01,0.02,0.02]`, `periods_per_year=1` | Sharpe `1/sqrt(3)`, Sortino `sqrt(3)` |

## 8. 함수명과 wire/backtest 명칭 매핑

이 표는 의미만 고정한다. S1.4에서는 serializer, RPC 또는 artifact schema를 구현하지 않는다.

| Python 함수 | wire/backtest 명칭 |
|---|---|
| `cagr` | `cagr` |
| `max_drawdown` | `mdd` |
| `sharpe_ratio` | `sharpe` |
| `sortino_ratio` | `sortino` |
| `historical_var(confidence=0.95)` | `var95` |
| `historical_cvar(confidence=0.95)` | `cvar95` |
| `realized_volatility` | 기존 realized-volatility 계열 필드의 계산 의미 |
| `annualized_volatility` | 기존 annualized-volatility 계열 필드의 계산 의미 |

`shared-docs/backtest_config.yaml`의 `annualized_return`은 v1에서 CAGR 공식과 동일한 legacy
선택 키다. canonical Python 함수와 향후 metric field는 `cagr`다.
`annualized_return`이라는 별도 `산술평균 × 252` estimator를 만들지 않으며 YAML key는 이번
세션에서 rename하지 않는다. 별도 계약 승인 없이 `cagr`와 다른 annualized-return 공식을
추가하지 않는다.

## 9. 가정·한계·금지 주장

- 표본 통계는 입력 자료와 선택한 estimator의 기술통계일 뿐 미래 분포를 보장하지 않는다.
- 기본 252와 `sqrt(252)`는 일봉 프로젝트 convention이며 자기상관·regime·volatility
  clustering을 자동 보정하지 않는다.
- `realized_volatility`는 intraday realized variance estimator가 아니다.
- threshold-tail CVaR는 exact finite-sample ES가 아니다.
- historical VaR의 subadditivity를 보장하지 않는다.
- 손계산 fixture 통과는 계산 정확성 근거이지 calibration, OOS 성능, 경제적 유효성,
  유동성·capacity·crowding 또는 alpha 지속성 근거가 아니다.
- pure function을 대수적으로 멱등이라고 부르지 않는다.
- lambda나 generator 문법이 purity를 보장한다고 주장하지 않는다.
- S1.4 결과만으로 투자 추천, 주문 허용, 공정가치 또는 손실 한도를 결정하지 않는다.

exact finite-sample ES, intraday realized volatility, Lo-adjusted Sharpe, PSR/DSR,
Kupiec/Christoffersen 검정, BSM/Greeks/IV, HMM, Monte Carlo, JAX와 다른 언어 구현은
S1.4R/X 또는 후속 세션 범위다.

## 10. primary/official source bibliography

긴 원문을 복사하지 않고 식별자와 이 프로젝트가 채택한 범위만 기록한다. 분류는 S1.4
공식을 직접 지지하는 `ADOPTED`, 연환산·해석 한계를 보조하는 `SUPPORTING`, 후속 연구
후보인 `RESEARCH_ONLY`, 관련 근거가 있어도 v1 호환 계약의 대체안으로는 채택하지 않는
`REJECTED_FOR_V1`, 함수형·부동소수점 배경인 `CONCEPTUAL`의 다섯 가지다.

| 분류 | 근거 | S1.4 채택 경계 |
|---|---|---|
| `ADOPTED` | [Sharpe, 1994, *The Sharpe Ratio*](https://doi.org/10.3905/jpm.1994.409501), [author copy](https://web.stanford.edu/~wfsharpe/art/sr/SR.htm) | 같은 주기 excess-return reward-to-variability 해석을 채택하되 기본 risk-free와 annualization은 프로젝트 convention으로 고정 |
| `SUPPORTING` | [Lo, 2002, *The Statistics of Sharpe Ratios*](https://doi.org/10.2469/faj.v58.n4.2453) | 자기상관이 단순 연환산 해석을 제한한다는 근거; v1에 Lo-adjusted estimator는 미채택 |
| `ADOPTED` | [Sortino and van der Meer, 1991, *Downside Risk*](https://doi.org/10.3905/jpm.1991.409343) | 목표 이하 위험과 대칭 변동성의 구분 |
| `ADOPTED` | [Sortino and Price, 1994, *Performance Measurement in a Downside Risk Framework*](https://doi.org/10.3905/joi.3.3.59) | MAR 기반 downside framework의 해석 근거; v1 분모의 exact shape는 이 문서가 고정 |
| `ADOPTED` | [Fishburn, 1977, *Mean-Risk Analysis with Risk Associated with Below-Target Returns*](https://ideas.repec.org/a/aea/aecrev/v67y1977i2p116-26.html) | below-target risk 개념 근거 |
| `SUPPORTING` | [GIPS Standards Handbook for Firms](https://www.gipsstandards.org/standards/gips-standards-for-firms/gips-standards-handbook-for-firms/) | 1년 미만 성과를 외부 GIPS 준수 연환산 성과로 표시하지 않는 보고 한계 |
| `SUPPORTING` | [Magdon-Ismail et al., 2004, *On the Maximum Drawdown of a Brownian Motion*](https://doi.org/10.1239/jap/1077134674) | maximum drawdown 개념의 연구 배경; v1 discrete signed algorithm은 이 문서가 고정 |
| `ADOPTED` | [Hyndman and Fan, 1996, *Sample Quantiles in Statistical Packages*](https://doi.org/10.1080/00031305.1996.10473566) | quantile estimator 식별 체계; type 7 추천 주장 없이 NumPy linear 호환을 선택 |
| `RESEARCH_ONLY` | [Artzner et al., 1999, *Coherent Measures of Risk*](https://doi.org/10.1111/1467-9965.00068) | coherent risk measure 배경; historical VaR subadditivity를 주장하지 않음 |
| `RESEARCH_ONLY` | [Rockafellar and Uryasev, 2000, *Optimization of Conditional Value-at-Risk*](https://doi.org/10.21314/JOR.2000.038) | CVaR/ES 연구 배경; v1 threshold-tail estimator를 교체하지 않음 |
| `RESEARCH_ONLY` | [Rockafellar and Uryasev, 2002, *Conditional Value-at-Risk for General Loss Distributions*](https://doi.org/10.1016/S0378-4266(02)00271-6) | 일반 loss distribution의 exact CVaR 연구; S1.4R 후보 |
| `RESEARCH_ONLY` | [Acerbi and Tasche, 2002, *On the Coherence of Expected Shortfall*](https://doi.org/10.1016/S0378-4266(02)00283-2) | Expected Shortfall 연구; v1 threshold-tail CVaR의 alias가 아님 |
| `REJECTED_FOR_V1` | [Rockafellar and Uryasev, 2002](https://doi.org/10.1016/S0378-4266(02)00271-6), [Acerbi and Tasche, 2002](https://doi.org/10.1016/S0378-4266(02)00283-2) | exact finite-sample ES로 v1 threshold-tail CVaR를 교체하는 안은 호환 계약 때문에 미채택; 별도 이름과 fixture의 S1.4R에서만 검토 |
| `RESEARCH_ONLY` | [Andersen et al., 2003, *Modeling and Forecasting Realized Volatility*](https://doi.org/10.1111/1468-0262.00418) | intraday realized volatility 연구; daily sample-volatility 호환 함수와 분리 |
| `CONCEPTUAL` | [IEEE 754-2019](https://doi.org/10.1109/IEEESTD.2019.8766229) | binary floating-point와 exceptional result 처리 배경 |
| `CONCEPTUAL` | [Goldberg, 1991, *What Every Computer Scientist Should Know About Floating-Point Arithmetic*](https://doi.org/10.1145/103162.103163) | 부동소수점 오차·범위·반올림의 구현 배경 |
| `ADOPTED` | [NumPy `std`](https://numpy.org/doc/stable/reference/generated/numpy.std.html) | `ddof=1`, float64 계산 convention |
| `ADOPTED` | [NumPy `quantile`](https://numpy.org/doc/stable/reference/generated/numpy.quantile.html) | `method="linear"`과 overwrite 금지 |
| `CONCEPTUAL` | [NumPy `log1p`](https://numpy.org/doc/stable/reference/generated/numpy.log1p.html) | 작은 단순수익률의 독립 검산과 underflow/precision 배경 |
| `ADOPTED` | [NumPy `array`](https://numpy.org/doc/stable/reference/generated/numpy.array.html), [copies and views](https://numpy.org/doc/stable/user/basics.copies.html) | defensive float64 copy와 no-alias 정책 |
| `ADOPTED` | [NumPy `errstate`](https://numpy.org/doc/stable/reference/generated/numpy.errstate.html) | 전역 상태가 아닌 지역 수치 오류 정책 |
| `CONCEPTUAL` | [Python 3.12 floating-point tutorial](https://docs.python.org/3.12/tutorial/floatingpoint.html) | binary floating-point의 표현 오차와 tolerance 배경 |
| `CONCEPTUAL` | [Python lambda reference](https://docs.python.org/3/reference/expressions.html#lambdas) | lambda는 문법 표현이며 purity 보장이 아니라는 구분 |
| `CONCEPTUAL` | [PEP 255, Simple Generators](https://peps.python.org/pep-0255/) | generator의 one-shot suspended state 때문에 reusable input 계약에서 제외 |
