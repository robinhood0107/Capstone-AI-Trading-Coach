# P1 Return 수익 검증 하네스

exact-31 LSTM 후보 생성기가 **실제로 수익에 기여하는지**를 21년 out-of-sample로 판정한다.
`modelQuality`를 `PASS`와 `BELOW_BASELINE` 중 무엇으로 공개할지의 근거가 여기서 나온다.

## 왜 필요했는가

계약 split의 test 구간(2026-01-19~)은 유니버스가 연 70% 오른 폭등장이다. 그 구간에서
상위 종목에 집중하면 좋은 수치가 나오지만 그것은 실력이 아니라 베타다. 그리고 여러 변형을
돌려 최고를 고르면 모든 후보가 순수 노이즈여도 최대 Sharpe가 부풀려진다
(Bailey & López de Prado, Deflated Sharpe Ratio).

그래서 단일 split 대신 **point-in-time walk-forward**로 재고, 판정 기준을 실행 전에
확정하고, 다중검정을 보정한다.

## 방법

| 항목 | 값 |
|---|---|
| 기간 | 2005 ~ 2026 test 연도, 22 fold |
| fold | 3년 train / 1년 test, 매년 롤 |
| 유니버스 | point-in-time. 그 시점에 상장돼 있고 train 구간이 확보된 종목만 |
| 타깃 | 로그수익률 `log(Close_t / Close_{t-1})`의 1일 선행 |
| 모델 | 계약 설정 그대로 — feature 9개, window 20, hidden 64, layer 1, dropout 0, Adam lr 0.001, SmoothL1, batch 32, epochs 10, seed 0, 종목별 독립 |
| scaler | `StandardScaler`, **train 구간으로만 fit** |
| 선택 | `predLogRet` 상위 5종목 균등 보유 (계약의 동시보유 5개 상한) |
| 보유기간 | 5 / 20 / 60 거래일 |
| 비용 | 회전에 왕복 35bps |
| 벤치마크 | 같은 PIT 유니버스 균등가중, 20일 리밸런싱 |
| 지표 | S1.4 `app.financial_engineering` (`sharpe_ratio`, `sortino_ratio`, `max_drawdown`, `annualized_volatility`, `cagr`, `simple_returns`) |

파라미터 탐색을 하지 않는다. 요청서의 `hyperparameterSearchCount=0`을 지킨다. 보유기간
3개는 사전 확정한 정책 레버이고 그 시행 수를 DSR에 넣는다.

### 판정 기준 (실행 전 확정)

- **채택**: PIT 균등가중 벤치마크를 Sharpe로 넘고, DSR 보정 후에도 유의하고, 약세 연도에
  벤치마크보다 낫다 — 셋 모두
- **기각**: 하나라도 못 넘으면 `modelQuality=BELOW_BASELINE`으로 공개한다

결과를 보고 기준을 바꾸지 않는다.

## 실행

두 단계로 나눈다. torch는 return-engine venv에만 있고 S1.4의 statsmodels/hmmlearn은
decision-platform venv에만 있다. 새로 설치해 맞추지 않고 각자 있는 곳에서 돌린다.

```bash
cd workspaces/return-engine && uv run --frozen python ../decision-platform/research/p1-return-profit-verification/collect_history.py
```
```bash
cd workspaces/return-engine && uv run --frozen python ../decision-platform/research/p1-return-profit-verification/walk_forward.py
```
```bash
cd workspaces/decision-platform/python-services && uv run --frozen python ../research/p1-return-profit-verification/portfolio_eval.py
```

1단계는 yfinance를 호출한다. KIS·계좌·주문 호출은 0이다. 중간 산출물은 `/tmp/p1exp/`에
쓰고 Git에 넣지 않는다. 22 fold × 최대 31종목 = 543회 학습에 약 4분 30초 걸린다.

## 결과

`reports/profit-verification.v1.json`과 `reports/profit-verification.md`를 보라.

요약: **`BELOW_BASELINE`**. 방향 정확도 0.4777로 동전던지기 95% 구간(0.4973~0.5027)
아래이고, 벤치마크 초과분이 연 +0.22~+2.24%인데 추적오차가 16%라 t값이 0.06~0.64다.
초과분이 0과 구분되지 않는 반면 변동성은 22.9% → 29.5%로, MDD는 −49% → −55~−67%로
나빠진다. **알파는 없고 위험만 늘어난다.**

로그수익률 타깃 자체는 값의 건전성을 확실히 고쳤다 — `expectedReturn` 범위가
−4.72%~+5.15%다. 절대가 + MinMaxScaler 조합에서는 −84.86%~+4.28%였다.

## 한계 (공시)

- **종목 선정 생존편향이 남는다.** exact-31은 현재 시점 명부다. 과거에 상위였다가 탈락한
  종목이 없어 "오늘의 승자가 과거에 어땠는가"를 재며 달성 가능 수익을 과대평가한다.
  계약이 exact-31을 고정하므로 제거할 수 없다. 그래서 전략과 벤치마크에 같은 편향이
  들어가 상쇄되는 **PIT 균등가중 대비**로 판정한다. KOSPI 지수 대비 초과분을 알파라고
  주장하지 않는다.
- yfinance 가격은 수정주가가 아닌 `Close`(`auto_adjust=False`)다. 계약의
  `priceBasis=RAW_CLOSE`와 같은 기준이므로 운영과 일치하지만, 액면분할·배당의 영향은
  가격 계열에 그대로 남는다.
- 시뮬레이터는 보유기간 안의 실현 비중 표류를 추적하지 않고 목표 비중을 유지한다고 본다.
  회전 비용은 리밸런싱 시점의 목표 비중 변화로만 계산한다.
