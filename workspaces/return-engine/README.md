# Return Engine

주식 데이터를 기반으로 가격을 예측하고, 매매 전략을 백테스트하여 성과를 평가하는 Return Engine입니다.

현재 구현은 `ReturnEngine`을 진입점으로 사용하며, 데이터 수집부터 Feature 생성, LSTM 예측, Rule-based Baseline, 백테스트, Artifact 생성까지의 전체 과정을 하나의 실행 흐름으로 처리합니다.

---

## 1. 주요 기능

- `yfinance`를 이용한 주가 데이터 수집
- CSV 기반 주가 데이터 관리
- 주가 데이터 전처리
- 기술적 지표 생성
  - Diff
  - MA5
  - MA20
  - RSI
- LSTM 기반 주가 예측
- Rule-based Baseline 전략
  - MA5 / MA20 Golden Cross
  - MA5 / MA20 Dead Cross
  - RSI 조건
- LSTM 예측 기반 매매 신호 생성
- 매매 신호 기반 백테스트
- 성과 지표 계산
  - Profit
  - Maximum Drawdown (MDD)
  - Calmar Ratio
  - Sharpe Ratio
  - Win Rate
  - Maximum Peak
  - Final Asset
  - Trade Count
- 최근 예측 결과 및 백테스트 결과의 JSON Artifact 생성

---

## 2. 프로젝트 구조

```text
return_engine/
│
├── src/
│   ├── return_engine.py
│   │
│   ├── artifact/
│   │   └── generator.py
│   │
│   ├── backtest_core/
│   │   ├── backtest_engine.py
│   │   └── signal_generator.py
│   │
│   ├── dataloader/
│   │   ├── datapileline.py
│   │   ├── preprocessor.py
│   │   └── stockdataloader.py
│   │
│   └── models/
│       ├── lstm.py
│       └── rule_baseline.py
│
├── data/
│   ├── stock/
│   │   └── {stock_code}.csv
│   │
│   └── model/
│       └── {stock_code}_lstm.pth
│
├── artifacts/
│   └── {stock_code}.json
│
├── .gitignore
└── README.md
```

### 디렉터리 역할

| 경로 | 역할 |
|---|---|
| `src/return_engine.py` | 전체 Return Engine 실행을 담당하는 진입점 |
| `src/dataloader/` | 주가 데이터 로딩, 전처리, Dataset/DataLoader 생성 |
| `src/models/` | LSTM 및 Rule-based Baseline 모델 |
| `src/backtest_core/` | 매매 신호 생성 및 백테스트 |
| `src/artifact/` | 결과 Artifact JSON 생성 |
| `data/stock/` | 종목별 주가 CSV 저장 |
| `data/model/` | 학습된 LSTM 모델 저장 |
| `artifacts/` | 최종 예측 및 백테스트 결과 JSON 저장 |

---

## 3. 전체 실행 흐름

```text
yfinance
   │
   ▼
StockDataLoader
   │
   ▼
data/stock/{stock_code}.csv
   │
   ▼
Preprocessor
   │
   ├── Diff
   ├── MA5
   ├── MA20
   └── RSI
   │
   ▼
Train / Validation / Test Split
   │
   ├──────────────────────┐
   ▼                      ▼
DataPipeline           Rule Baseline
   │                      │
   ▼                      ▼
LSTM Model            Baseline Signal
   │
   ▼
LSTM Prediction
   │
   ▼
LSTM Signal
   │
   └──────────┬───────────┘
              ▼
       BacktestEngine
              │
              ▼
       Performance Metrics
              │
              ▼
       ArtifactGenerator
              │
              ▼
artifacts/{stock_code}.json
```

---

## 4. 실행 진입점

전체 실행은 `ReturnEngine` 클래스를 통해 수행합니다.

```python
from src.return_engine import ReturnEngine

engine = ReturnEngine(
    stock_name="Samsung Electronics",
    stock_code="005930.KS"
)

engine.run()
```

`run()`을 호출하면 다음 과정이 순차적으로 수행됩니다.

1. `yfinance`에서 주가 데이터 다운로드
2. CSV 저장 및 데이터 로딩
3. Feature 생성
4. Train / Validation / Test 데이터 분할
5. LSTM 학습 또는 기존 모델 로드
6. Test 데이터에 대한 LSTM 예측
7. LSTM 매매 신호 생성
8. Rule-based Baseline 매매 신호 생성
9. Baseline 백테스트
10. LSTM 백테스트
11. 다음 거래일 가격 예측
12. 최근 예측 및 백테스트 결과를 Artifact JSON으로 저장

---

## 5. 데이터 수집

현재 개발 버전에서는 `StockDataLoader`가 `yfinance`를 사용하여 데이터를 수집합니다.

```python
StockDataLoader.download(
    stock_code,
    start="2020-01-01",
    end=datetime.date.today(),
    path=stock_path
)
```

수집된 데이터는 다음 위치에 저장됩니다.

```text
data/stock/{stock_code}.csv
```

현재 `ReturnEngine`은 실행할 때마다 지정된 기간의 주가 데이터를 다운로드하여 CSV를 갱신합니다.

> 실제 프로젝트 통합 단계에서는 Decision Platform에서 제공하는 계약된 데이터를 소비하는 구조로 변경할 수 있습니다. Return Engine은 KIS API를 직접 호출하는 것을 책임지지 않습니다.

---

## 6. Feature 및 전처리

`Preprocessor`에서 다음 Feature를 생성합니다.

```text
Open
High
Low
Close
Adj Close
Diff
MA5
MA20
RSI
Volume
```

### Feature 설명

- `Diff`: 종가의 전일 대비 변화율
- `MA5`: 5일 이동평균
- `MA20`: 20일 이동평균
- `RSI`: 14일 기준 Relative Strength Index

Feature 생성 후 결측값을 제거하고 시간 순서에 따라 데이터를 Train / Validation / Test로 분할합니다.

기본 분할 비율은 다음과 같습니다.

```text
Train      80%
Validation 10%
Test       10%
```

LSTM 입력 데이터는 기본 Window Size `20`을 사용합니다.

---

## 7. LSTM Model

LSTM 모델은 주가의 다음 시점 가격을 예측하는 회귀 모델입니다.

현재 기본 설정은 다음과 같습니다.

```text
Hidden Size   : 128
Num Layers    : 3
Learning Rate : 0.0005
Window Size   : 20
Loss          : SmoothL1Loss
Optimizer     : Adam
```

모델은 CUDA 사용이 가능하면 GPU를 사용하고, 그렇지 않으면 CPU를 사용합니다.

학습이 완료된 모델은 다음 위치에 저장됩니다.

```text
data/model/{stock_code}_lstm.pth
```

### 모델 로딩 정책

해당 종목의 모델 파일이 존재하지 않는 경우:

```text
데이터 → 학습 → 모델 저장
```

모델 파일이 이미 존재하는 경우:

```text
모델 로드 → 예측
```

방식으로 동작합니다.

---

## 8. Rule-based Baseline

LSTM 모델의 성능을 비교하기 위한 Rule-based Baseline을 제공합니다.

현재 Baseline은 MA5와 MA20의 교차 및 RSI를 사용하여 매매 신호를 생성합니다.

### BUY

```text
Golden Cross
AND
RSI < 70
```

### SELL

```text
Dead Cross
AND
RSI > 30
```

조건을 만족하지 않는 경우 `HOLD` 신호를 생성합니다.

---

## 9. LSTM Signal

LSTM의 예측 가격 변화율을 기반으로 매매 신호를 생성합니다.

현재 기본 threshold는 다음과 같습니다.

```text
BUY  threshold : 0.005
SELL threshold : 0.005
```

예측 변화율이 매수 threshold보다 크면 `BUY`,
매도 threshold보다 작으면 `SELL`,
그 외에는 `HOLD`를 생성합니다.

---

## 10. Backtest

`BacktestEngine`은 생성된 매매 신호를 순차적으로 실행하여
포트폴리오 자산 변화를 계산합니다.

기본 초기 자본은 다음과 같습니다.

```text
10,000,000
```

현재 거래 방식은 다음과 같습니다.

- `BUY`: 사용 가능한 현금으로 최대한 많은 주식 매수
- `SELL`: 보유한 주식을 전량 매도
- 거래 시점의 가격을 기준으로 포트폴리오 평가

현재 구현에서는 거래 비용 및 Slippage를 별도로 적용하지 않습니다.

---

## 11. Performance Metrics

백테스트 종료 후 다음 성과 지표를 계산합니다.

```text
Profit
MDD
Calmar Ratio
Sharpe Ratio
Win Rate
Max Peak
Final Asset
Trade Count
```

### Profit

초기 자산 대비 최종 자산의 수익률입니다.

```text
(Final Asset - Initial Cash) / Initial Cash
```

### MDD

일별 포트폴리오 자산의 최고점 대비 최대 하락폭을 계산합니다.

### Sharpe Ratio

일별 포트폴리오 수익률을 기반으로 연환산 Sharpe Ratio를 계산합니다.

### Calmar Ratio

수익률을 MDD 절댓값으로 나누어 계산합니다.

---

## 12. Artifact

실행이 완료되면 `ArtifactGenerator`를 통해 JSON 결과를 생성합니다.

저장 위치:

```text
artifacts/{stock_code}.json
```

Artifact에는 다음 정보가 포함됩니다.

```json
{
    "stock_code": "...",
    "date": "...",
    "prediction": {
        "prediction": "...",
        "prediction_change": "..."
    },
    "recent_prediction": [
        {
            "date": "...",
            "actual": "...",
            "prediction": "...",
            "actual_change": "...",
            "prediction_change": "..."
        }
    ],
    "backtest": {
        "baseline_model": {},
        "lstm_model": {}
    }
}
```

### Artifact 구성

- `stock_code`: 종목 코드
- `date`: 예측 기준 날짜
- `prediction`: 다음 시점 예측 가격 및 변화율
- `recent_prediction`: 최근 5개 실제 가격 및 예측 결과
- `backtest.baseline_model`: Rule-based Baseline 백테스트 결과
- `backtest.lstm_model`: LSTM 백테스트 결과

---

## 13. 주요 클래스

### `ReturnEngine`

전체 Return Engine의 실행 흐름을 관리합니다.

```text
ReturnEngine.run()
    ├── StockDataLoader
    ├── Preprocessor
    ├── DataPipeline
    ├── LSTMModel
    ├── BaselineModel
    ├── SignalGenerator
    ├── BacktestEngine
    └── ArtifactGenerator
```

### `StockDataLoader`

`yfinance`를 이용하여 주가 데이터를 다운로드하고 CSV로 저장합니다.

### `Preprocessor`

Feature 생성, 데이터 분할, Scaling 및 시계열 Sequence 생성을 담당합니다.

### `DataPipeline`

전처리된 데이터를 LSTM 학습에 사용할 PyTorch `DataLoader` 형태로 변환합니다.

### `LSTMModel`

LSTM 모델의 학습, 검증, 예측, 미래 가격 Forecast 및 모델 저장/로드를 담당합니다.

### `BaselineModel`

Rule-based Baseline 전략의 신호 생성을 담당합니다.

### `SignalGenerator`

Baseline 및 LSTM 예측 결과를 `BUY / HOLD / SELL` 신호로 변환합니다.

### `BacktestEngine`

매매 신호를 실행하고 포트폴리오 및 성과 지표를 계산합니다.

### `ArtifactGenerator`

예측 결과와 백테스트 결과를 계약된 JSON Artifact 형태로 저장합니다.

---

## 14. 현재 구현 기준 의존 관계

```text
return_engine.py
    │
    ├── dataloader
    │     ├── stockdataloader
    │     ├── preprocessor
    │     └── datapileline
    │
    ├── models
    │     ├── lstm
    │     └── rule_baseline
    │
    ├── backtest_core
    │     ├── signal_generator
    │     └── backtest_engine
    │
    └── artifact
          └── generator
```

각 모듈은 `ReturnEngine`에서 조합되며,
사용자는 개별 모듈의 실행 순서를 직접 관리하지 않고 `ReturnEngine.run()`을 호출하여 전체 파이프라인을 실행할 수 있습니다.

---

## 15. 개발 및 통합 방향

현재 구현은 독립적인 Return Engine 개발 및 검증을 목적으로 구성되어 있습니다.

향후 팀 공용 Repository에 통합할 때는 프로젝트 전체 명세에 따라 다음과 같은 역할 분리를 고려합니다.

```text
src/
├── data/
├── features/
├── lstm/
├── rule_baseline/
├── backtest_core/
└── artifact_export/
```

현재 구현의 핵심 실행 흐름과 모델/백테스트 로직은 유지하면서,
Decision Platform과의 데이터 및 Artifact 계약에 맞게 입출력 계층을 통합하는 것을 목표로 합니다.

---

## P1 full-app v2 수신 preview 경계

- 현재 CSV, PTH, legacy JSON과 원본 source/cache는 receipt로 보존한다.
- Compose 기본 실행은 provider 호출 0이며 PTH SHA-256 확인과 `weights_only=True`를 강제한다.
- 현재 결과는 `LEGACY_RECEIVED_PREVIEW`, `realTeamB=false`다.
- exact artifact 10개와 manifest가 오기 전까지 `TEAM_B_REAL_ARTIFACT=BLOCKED`다.
- 실행: `uv run --frozen python src/preview_cli.py run --csv data/stock/005930.KS.csv --pth data/model/005930.KS_lstm.pth --output /tmp/005930.KS.json`
