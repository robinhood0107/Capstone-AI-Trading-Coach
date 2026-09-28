# P1 Return 수익 검증 결과

측정일 2026-09-03. 22 fold walk-forward, test 연도 2005~2026, 예측 130,752행, 31종목. 정확한 값은 [기계 판독 산출물](profit-verification.v1.json)과 대조한다.
왕복 35bps. 지표는 S1.4 `app.financial_engineering`.

**판정: `modelQuality=BELOW_BASELINE`.** 사전 확정한 기준 셋 중 어느 것도 통과하지 못했다.

## 1. 예측력 자체가 없다

| 항목 | 값 |
|---|---|
| 방향 정확도 | **0.4777** |
| 동전던지기 95% 구간 | 0.4973 ~ 0.5027 (n=130,752) |
| RMSE (모델) | 0.027346 |
| RMSE (naive = 0 예측) | 0.026245 |

표본이 13만 관측이라 표준오차가 0.0014다. **0.4777은 동전던지기 구간 아래**이므로
"신호가 없다"가 아니라 **부호가 통계적으로 유의하게 반대**다. 그리고 "항상 0을 예측한다"는
naive 기준선보다 RMSE가 크다 — 모델이 만드는 변동이 오차를 줄이지 못하고 늘린다.

앞선 단일 split 실험(`dir_acc` 0.481~0.499)은 test 세션 126개로 표준오차가 0.008이라
동전던지기와 구분할 수 없었다. 22 fold로 표본을 33배 늘려서야 방향이 확정됐다.

## 2. 상위 k 선택이 벤치마크에 아무것도 더하지 않는다

| 전략 | 연수익 | 변동성 | Sharpe | Sortino | MDD |
|---|---|---|---|---|---|
| **고정 exact-31 균등가중 기준선** | 20.7% | 22.9% | **0.94** | 1.35 | **−49.4%** |
| LSTM 상위5 / 5일 보유 | 21.3% | 29.6% | 0.80 | 1.20 | −58.2% |
| LSTM 상위5 / 20일 보유 | 18.8% | 29.9% | 0.72 | 1.06 | −66.7% |
| LSTM 상위5 / 60일 보유 | 19.4% | 29.1% | 0.75 | 1.11 | −55.0% |

기준선 유니버스는 2026-09 시점의 고정 exact-31 목록이어서 종목 선정 생존편향이 남는다. 세 후보 모두 기준선보다 Sharpe가 낮고 MDD가 깊다. 보유기간을 늘려 비용을 줄여도
(회전 드래그 11.1%p → 1.7%p) 뒤집히지 않는다 — 이 결과로 성과 주장을 할 수 없다.

### 초과분은 0과 구분되지 않는다

이게 결정적인 숫자다.

| 전략 | 연 초과수익 | 추적오차 | Information Ratio | t |
|---|---|---|---|---|
| LSTM 상위5 / 5일 보유 | +2.19% | 16.25% | +0.135 | **+0.62** |
| LSTM 상위5 / 20일 보유 | +0.17% | 16.11% | +0.010 | **+0.05** |
| LSTM 상위5 / 60일 보유 | +0.45% | 15.74% | +0.029 | **+0.13** |

21.3년을 모아도 초과수익 t값은 0.62 이하라 0과 구분되지 않는다. 반면 변동성은
22.9% → 29.1~29.9%, MDD는 −49.4% → −55.0~−66.7%로 나빠진다.
**알파는 만들지 못하면서 위험은 실제로 늘린다.**

## 3. Deflated Sharpe Ratio를 오해하지 않는다

| 전략 | 관측 Sharpe | σ(SR) | 기대 최대 | DSR z | p |
|---|---|---|---|---|---|
| LSTM 상위5 / 5일 보유 | 0.80 | 0.22 | 0.18 | 2.87 | 0.0021 |
| LSTM 상위5 / 20일 보유 | 0.72 | 0.22 | 0.18 | 2.49 | 0.0064 |
| LSTM 상위5 / 60일 보유 | 0.75 | 0.22 | 0.18 | 2.63 | 0.0042 |

DSR은 유의하다. 그러나 **DSR이 답하는 질문은 "Sharpe가 0보다 큰가"이고 "벤치마크를
넘는가"가 아니다.** 한국 대형주가 21년간 올랐으므로 아무 롱온리 포트폴리오나 Sharpe가
0보다 크다. 초과분 t값(2번)이 우리가 실제로 물어야 하는 검정이다.

DSR을 근거로 "통계적으로 유의한 전략"이라고 말하는 것이 바로 Bailey & López de Prado가
경고하는 오독이다.

## 4. 선택 연도별 결과는 시장 구간에 따라 달라진다

| 연도 | 벤치마크 | 상위5/5일 | 상위5/20일 | 상위5/60일 |
|---|---|---|---|---|
| 2008 | −24.8% | −29.8% | −47.7% | −34.5% |
| 2011 | −12.6% | −7.5% | −10.0% | −18.9% |
| 2014 | −5.0% | −0.6% | −6.8% | +1.0% |
| 2015 | −4.7% | −7.6% | −12.0% | −12.4% |
| 2018 | −13.9% | −1.9% | −12.6% | −21.8% |
| 2020 | +45.8% | +17.5% | +7.4% | +39.1% |
| 2022 | −8.5% | −15.4% | −10.7% | −16.5% |

표에는 벤치마크가 하락한 연도와 상승한 2020년을 함께 넣었다. 2008년 20일 보유는 −47.7%로
벤치마크(−24.8%)보다 더 크게 하락했고, 상승장인 2020년에는 벤치마크 수익률을 따라가지
못했다. 일부 연도만으로 성과를 일반화하지 않는다.

## 5. 폭등장 표본 문제가 실재한다는 증거

2026년 9월 초까지의 YTD 구간에서는 상위5 전략이 +107.8~+118.6%, 기준선이 +48.2%였다.
하지만 2025년에는 기준선 +97.7%가 상위5 전략(+80.9~+88.1%)보다 높았다. **강한 상승 구간만
선택하면 우위를 과장할 수 있다.** 21.3년 결과에서는 초과수익이 통계적으로 확인되지 않았다.

이것이 단일 split 결과를 신뢰할 수 없다고 판정한 이유의 실측 근거다.

## 6. 로그수익률 타깃은 예측력이 아니라 값의 건전성을 고쳤다

| | 절대가 + MinMaxScaler | 로그수익률 + StandardScaler |
|---|---|---|
| `expectedReturn` 범위 | −84.86% ~ +4.28% | **−4.72% ~ +5.15%** |

절대가 타깃은 test 구간이 train 최대값을 100% 초과할 때 역변환 상한에 갇혀
`expectedReturn −85%`를 만든다. 그 값이 RiskEngine 수량 결정과 대시보드 표시로 흘러간다.
**예측이 틀리는 것은 받아들이지만 값이 미치는 것은 받아들일 수 없다.** 로그수익률 전환의
근거는 성능이 아니라 이것이다.

## 결론

1. **`modelQuality=BELOW_BASELINE`으로 공개한다.** 요청서 93행이 이를 허용한다.
2. **로그수익률 타깃은 채택한다.** 예측력 때문이 아니라 `expectedReturn`이 정상 범위에
   들어오기 때문이다.
3. **상위 k 선택 자체는 계약이 이미 하는 일이므로 유지한다.** 새 알파를 주지 않지만,
   자동매매의 안전은 모델 성능이 아니라 뒷단이 담보한다 — Strong LLM veto,
   RiskEngine 단독 수량 권위, 동시보유 5개 상한, 예산, 35bps, kill switch.
   LSTM은 후보 생성기이고 곧바로 주문이 되지 않는다.
4. **성능 개선을 이 축에서 더 시도하지 않는다.** 파라미터 탐색은 요청서의
   `hyperparameterSearchCount=0`이 금지하고, 실제로 필요한 것은 종목 수와 기간의 확대
   (문헌은 수백~수천 종목·롱숏 기준)로 승인된 데이터 경계를 바꾸는 별개 사안이다.

## 참고 문헌

- Bailey, D. H. & López de Prado, M. (2014), [The Deflated Sharpe Ratio](https://doi.org/10.3905/jpm.2014.40.5.094), *The Journal of Portfolio Management*, 40(5), 94–107; Bailey, D. H., Borwein, J. M., López de Prado, M. & Zhu, Q. J. (2017), [The Probability of Backtest Overfitting](https://doi.org/10.21314/JCF.2016.322), *The Journal of Computational Finance*, 20(4), 39–69. 다중검정과 선택 편향을 다룬다.
- Fischer, T. & Krauß, C. (2018), [Deep Learning with Long Short-Term Memory Networks for Financial Market Predictions](https://doi.org/10.1016/j.ejor.2017.11.054), *European Journal of Operational Research*, 270(2), 654–669. 다른 시장·유니버스·전략 설정의 선행 연구이며, 이 프로젝트와 직접적인 성과 비교로 사용하지 않는다.
- DeMiguel, V., Garlappi, L. & Uppal, R. (2009), [Optimal Versus Naive Diversification: How Inefficient Is the 1/N Portfolio Strategy?](https://doi.org/10.1093/rfs/hhm075), *Review of Financial Studies*, 22(5), 1915–1953. 표본 추정 오차와 1/N 기준선 비교의 근거다.
- López de Prado, M. (2018), [Advances in Financial Machine Learning](https://www.wiley.com/en-us/Advances+in+Financial+Machine+Learning-p-9781119482086), ch. 5. 이번 검증에서는 hyperparameter search를 수행하지 않았다.
- Fama, E. F. & MacBeth, J. D. (1973), [Risk, Return, and Equilibrium: Empirical Tests](https://doi.org/10.1086/260061), *Journal of Political Economy*, 81(3), 607–636. 날짜별 횡단면 평균의 시계열 검정.
- Newey, W. K. & West, K. D. (1987), [A Simple, Positive Semi-Definite, Heteroskedasticity and Autocorrelation Consistent Covariance Matrix](https://doi.org/10.2307/1913610), *Econometrica*, 55(3), 703–708. 5-lag HAC 표준오차.
