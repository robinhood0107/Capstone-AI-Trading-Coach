# Team B exact-31 model report

## Data

- 수집: yfinance, `auto_adjust=False`, 사용 컬럼 Open/High/Low/Close/Volume
- `Adj Close`는 쓰지 않는다. 계약이 `priceBasis=RAW_CLOSE`다.
- 유니버스: `contracts/catalogs/p1-return-universe.v1.json` exact-31
- 입력 해시(`inputPackSha256`): `8fa5e7177df66513c8923d7f7c5ad84d617fe0d8646b8baffac1620b6bc67d75`
- 마지막 공통 세션: `2026-09-01`
- 거래정지 바 449행을 제외했다. yfinance 는 거래정지일에도 직전 종가를
  그대로 채워 보내고 그 행은 OHLC 가 모두 종가와 같으며 거래량이 0이다. 가격 관측이
  아니므로 수집 단계에서 제외한다. 그대로 두면 14일 이상 이어질 때 RSI 의 gain/loss 가
  둘 다 0이 되어 `0/0 = NaN`이 되고 `dropna()`가 **중간** 거래일을 조용히 지운다.
  최다: 012450 34행, 207940 27행, 010120 19행.
- **yfinance 의 `Close`는 액면분할이 소급 반영된 값이다.** `auto_adjust=False`는 배당
  조정만 끄고 분할 조정은 남는다. 실제로 일부 종목의 과거 종가가 소수로 나온다
  (한국 주식 raw 종가는 정수다). 따라서 과거 구간은 엄밀한 `RAW_CLOSE`가 아니라
  분할 조정가다. 학습에는 연속성이 있는 편이 낫고, seed 의 `currentClose`는 최신
  세션이라 조정 대상이 아니다. 이 차이를 계약 주장으로 감추지 않고 여기 기록한다.

## Model ABI

- feature 9개 순서: `open, high, low, raw_close, volume, return_1d, ma5, ma20, rsi14`
- window 20 / hidden 64 / layer 1 / dropout 0.0
- `targetTransform=LOG_RETURN`. 모델은 로그수익률을 예측하고 `forecastClose = currentClose * exp(y-hat)` 로 가격을 재구성한다. 절대가 타깃은 test 가 train 최대를 넘으면 역변환이 학습 평균 근처로 주저앉아 `expectedReturn -85%` 를 만든다.
- optimizer Adam / lr 0.001 / loss SmoothL1 / batch 32 / epochs 10
- 종목별 독립 LSTM 31개, seed 0, thread 1
- scaler: `StandardScaler`, train 구간으로만 fit

## Split

- train: 처음 ~ `2025-08-06` 이전
- validation: `2025-08-06` ~ `2026-03-05` 이전 (120 세션)
- test: `2026-03-05` ~ `2026-09-01` (120 세션)
- 모든 종목이 같은 경계 날짜를 쓴다. 상장일이 달라 train 길이만 종목마다 다르다.
- validation 으로 설정을 바꾸지 않았고 final test 후 재튜닝은 0이다.

## Reproducibility

- 수집과 학습을 분리한다. `collect`만 네트워크를 쓰고 `export`는 캐시만 읽는다.
  그래서 `producer.networkCalls=0`이 정직하게 성립한다.
- KIS·계좌·잔고·주문 호출 0, Spring 호출 0.
- CPU 단일 스레드, seed 0.

## Model quality

- **`BELOW_BASELINE`**. 21년 walk-forward out-of-sample 측정 결과다.
- naive persistence 기준선 대조 (test 구간 3,191개 1스텝 예측, 실제 종가 대비 RMSE): 모델 62,685원 / naive(내일=오늘) 60,640원 -> 모델이 못하다
- test 구간 방향 정확도 0.4866 (실현 변동이 0이 아닌 3,144개 기준). 21년 walk-forward 에서도 0.4777 로 동전던지기 아래였다. 값의 건전성은 고쳤지만 방향 예측력은 없다.
- forecast/current 비율 0.9653 ~ 1.0177
- signal 분포 BUY 3 / HOLD 17 / SELL 11
- GUIDE 시나리오: netReturn 0.0361 / mdd -0.0419 / sharpe 1.0441 / 거래 22건
- 근거 전문은 `workspaces/decision-platform/research/p1-return-profit-verification/reports/`를 보라.

## Limitations

- 성능 주장을 하지 않는다 (`performanceClaimAllowed=false`).
- 주문 권한이 없다 (`orderAuthority=NONE`). LSTM 은 후보 생성기이고 곧바로 주문이
  되지 않는다. 수량은 RiskEngine 이 단독으로 정한다.
- 규칙 baseline 의 `forecastClose`는 **가격 예측이 아니다.** `from_baseline`이 가격을
  내지 않으므로 signal 을 계약 필드로 옮기기 위한 정규화 표현이다.
- 시나리오는 `GUIDE` 하나다. `scenario_policy.json`이 GUIDE 를 `OWNER_GUIDE_REPLAY`로 정의하고 `teamBRiskEngineImplementation=false`이므로
  의미가 완전히 맞지는 않는다. 이 절충을 기록해 둔다.
- 자산곡선은 Team B 엔진의 주문을 exporter 가 왕복 35bps 와 함께 재생한 것이다.
  Team B 엔진 자체에는 수수료 모델이 없다.
- 전액 매수 뒤 두 번째 BUY 는 `cash // price = 0`이 되어 조용히 무시된다. 무해하지만
  거래 수가 의도를 그대로 반영하지는 않는다.
- 종목 선정 생존편향이 남는다. exact-31 은 현재 시점 명부다.
