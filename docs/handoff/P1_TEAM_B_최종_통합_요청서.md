# Team B exact-31 최소 구현 요청서

> `OWNER_INPUT_RECEIPT=PASS`: exact-31 종목 집합과 confidence-free artifact 계약 검증을
> 마쳤다. 입력은 봉인 ZIP 이 아니라 커밋된 유니버스 카탈로그 + yfinance 자동 수집이다.
> Team B는 카탈로그에 적힌 31개 티커만 수집하며 임의 종목으로 대체하지 않는다.

이 문서 하나만 보고 `workspaces/return-engine/`만 작업해 주세요.

## 목표

기존 삼성전자 LSTM·rule baseline·전처리·백테스트를 재사용해 exact-31
production 학습과 exact-10 export를 완성해 주세요.

우선순위는 성능 개선이 아니라 **한 번에 실행되고 Owner가 바로 적재할 수 있는
실제 산출물**입니다.

## 시작 입력

입력은 repository 안의 유니버스 카탈로그 하나입니다.

```text
contracts/catalogs/p1-return-universe.v1.json
```

이 파일이 수집할 종목의 단일 진실입니다. 31개 항목에 6자리 종목코드와 그에 대응하는
`yfinanceTicker`가 들어 있고, exact-31 전원이 KOSPI이므로 접미사는 `.KS` 하나입니다.
`132030`(금 ETF)이 정확히 한 번 들어갑니다.

```text
가격 수집: yfinance, auto_adjust=False
사용 컬럼: Open, High, Low, Close, Volume
Adj Close: 사용하지 않음 (계약이 priceBasis=RAW_CLOSE)
```

기존 `StockDataLoader.download`를 그대로 재사용하시면 됩니다. 카탈로그에 없는 종목을
넣거나 임의 CSV로 대체하지 말아 주세요. 카탈로그에 있는 티커가 수집되지 않으면 조용히
건너뛰지 말고 실패로 종료해 주세요 — 31개 중 몇 개가 빠진 채로 학습이 끝나는 것이
가장 찾기 어려운 결함입니다.

사전 공유 SHA-256 대조는 없어졌습니다. `manifest.inputPackSha256`에는 실제로 사용한
입력의 정직한 해시를 넣어 주세요. Owner는 그 값을 기대값과 대조하지 않고 증거로
기록합니다.

수집 결과는 Git에 커밋하지 않습니다. 런타임 수집이므로 커밋할 입력 파일이 없습니다.

current artifact 계약은 confidence가 없는
`p1-return-engine-artifact-manifest.v3`입니다. Team B에서 confidence 필드를 다시 추가하거나
정책을 재설계하지 마세요.

## 필수 구현

### 1. production CLI

다음 명령을 구현해 주세요.

```bash
PYTHONPATH=src uv run python -m return_engine verify-input \
  --universe-catalog <repository-root>/contracts/catalogs/p1-return-universe.v1.json

PYTHONPATH=src uv run python -m return_engine train-production \
  --universe-catalog <repository-root>/contracts/catalogs/p1-return-universe.v1.json \
  --output-root <repository-root>/deploy/p1/seed/team-b
```

`verify-input`은 카탈로그를 읽고 31개 티커가 실제로 수집되는지 확인합니다.
`train-production`은 수집부터 학습·백테스트·exact-10·manifest export까지 한 번에
완료해야 합니다. 기존 output을 덮어쓰지 말고 새 폴더에만 성공해야 합니다.

`--manifest-sha256`과 `OWNER_INPUT_PACK_REQUIRED` 종료는 없어졌습니다. 그리고
`_resolve_daily_ohlcv`/`_resolve_manifest_file`의 `rglob("*.csv")[0]`·`rglob("*.json")[0]`
폴백은 제거해 주세요 — 지정한 파일이 없을 때 폴더에서 아무 파일이나 골라 조용히 학습
입력으로 삼습니다. 이 경로는 실패해야 맞습니다.

### 2. 고정 학습 설정

```text
universe=contracts/catalogs/p1-return-universe.v1.json 의 고정 exact-31
priceBasis=RAW_CLOSE
minimumHistory=756 XKRX sessions
featureOrder=open,high,low,raw_close,volume,return_1d,ma5,ma20,rsi14
windowSize=20
hiddenSize=64
layerCount=1
dropout=0
outputSize=1
optimizer=Adam
learningRate=0.001
loss=SmoothL1
batchSize=32
epochs=10
seed=0
threadCount=1
shuffle=false
roundTripCostBps=35
hyperparameterSearchCount=0
postFinalTuningCount=0
```

`hiddenSize`·`layerCount`·`dropout`·`learningRate`는 Team B가 정한 값입니다. Owner
런타임은 이 값들을 상수로 갖고 있었지만 `config.json`에서 파생하도록 바꿨으므로, 앞으로
더 조정해도 Owner 쪽 재작업이 없습니다. `windowSize=20`과 `outputSize=1`은 텐서 집합과
계약된 값이라 그대로 두셔야 합니다.

- 종목별 독립 LSTM 31개
- 모든 종목에 같은 시간 순서 split
- scaler는 train 구간으로만 fit
- validation으로 설정을 바꾸지 않고 final test 후 재튜닝 0
- corporate-action exclusion과 중간 거래일 누락을 fail-closed
- CPU 결정적 실행과 외부 네트워크 호출 0

성능이 baseline보다 낮아도 성공입니다. 수익률·Sharpe·baseline 우월을 만들기 위해 설정이나
데이터 경계를 바꾸지 마세요.

### 3. exact-10 Git Model Seed

`deploy/p1/seed/team-b/`에 다음 파일과 `p1-return-engine-manifest.v3.json`을 만들어 주세요.

```text
model.safetensors
scaler.json
config.json
lstm_signals.parquet
rule_baseline_signals.parquet
backtest_result.json
trade_log.parquet
equity_log.parquet
golden_output.json
model_report.md
```

- `model.safetensors`는 exact-31 namespace와 계약된 tensor shape를 사용
- PTH·pickle·joblib production 산출물 0
- model 단일 파일 90MiB 미만
- input raw data, cache, 학습 중간 output은 Git에 추가하지 않음
- `performanceClaimAllowed=false`, `orderAuthority=NONE`
- 기술 검증이 통과하면 성능은 `PASS | BELOW_BASELINE`으로 정직하게 기록

## 제외 범위

Team B는 다음을 구현하지 않습니다.

- KIS·KRX·ECOS 수집 (yfinance 가격 수집은 Team B 범위입니다)
- daily inference scheduler와 자동매매 runtime
- Spring·PostgreSQL·Compose·Dashboard
- 계좌·잔고·주문·Vertex·RiskEngine
- confidence 계약 재설계
- 자동 재학습·월별 universe 교체·성능 탐색
- OCI·GHCR·SBOM·서명·Kubernetes
- 별도 발표자료

기존 preview·CSV·PTH·테스트는 삭제하거나 불필요하게 다시 작성하지 마세요.

## 최소 검증

구현 중에는 다음 focused 검증만 실행해 주세요. Docker image build와 repository CI는
Owner가 마지막에 한 번 수행하므로 Team B가 반복하지 않습니다.

```bash
cd workspaces/return-engine
uv sync --frozen
uv run pytest -q
PYTHONPATH=src uv run python -m return_engine --help
```

전체 repository CI, full Compose E2E, 보안·공급망·KIS Mock 검증은 Owner가 Model Seed를 받은 뒤
마지막에 한 번만 실행합니다. Team B가 동일한 전체 검증을 반복하지 마세요.

## 제출물

1. PR URL과 commit SHA
2. 사용한 유니버스 카탈로그 SHA-256과 수집 종목 수
3. result manifest SHA-256
4. exact-10 hash 표
5. 학습 실행 시간과 결과
6. unit·CLI 결과
7. `PASS | BELOW_BASELINE`과 남은 blocker

```text
TEAM_B_CODE_IMPLEMENTATION=PASS
TEAM_B_REAL_ARTIFACT_V2=PASS
TEAM_B_ARTIFACT_SCHEMA=p1-return-engine-artifact-manifest.v3
TEAM_B_GIT_MODEL_SEED=PASS
TEAM_B_PROVIDER_ACCOUNT_ORDER_CALLS=0
TEAM_B_ORDER_AUTHORITY=NONE
```

Owner는 수신 후 Git Model Seed 검증·자동 import·daily inference·KIS Mock 연결을 담당합니다.
