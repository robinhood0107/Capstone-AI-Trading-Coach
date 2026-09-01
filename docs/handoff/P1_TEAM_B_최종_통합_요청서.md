# Team B exact-31 최소 구현 요청서

> `DRAFT_OWNER_INPUT_RECEIPT`: Owner 코드·confidence 제거·Git Seed import·V116 daily inference는
> 완료됐다. 다만 실제 756-session KIS input pack은 별도 물리 호출 승인이 필요하므로,
> 아래 manifest SHA-256이 채워지기 전에는 Team B에게 전달하지 않는다.

이 문서 하나만 보고 `workspaces/return-engine/`만 작업해 주세요.

## 목표

기존 삼성전자 LSTM·rule baseline·전처리·백테스트를 재사용해 exact-31
production 학습과 exact-10 export를 완성해 주세요.

우선순위는 성능 개선이 아니라 **한 번에 실행되고 Owner가 바로 적재할 수 있는
실제 산출물**입니다.

## 시작 입력

Owner가 다음 두 개를 제공합니다.

```text
1. 파일명: `p1-return-engine-input-pack.v1.zip`
2. manifest SHA-256: `BLOCKED_PENDING_APPROVED_KIS_INPUT_PACK`
```

ZIP을 Git 밖 local 폴더에 압축 해제한 뒤 manifest와 모든 파일의 크기·SHA-256를
먼저 검증해 주세요. 입력이 없거나 hash가 다르면 임의 CSV·yfinance로 대신하지 말고
`OWNER_INPUT_PACK_REQUIRED`로 종료해 주세요.

current artifact 계약은 confidence가 없는
`p1-return-engine-artifact-manifest.v3`입니다. Team B에서 confidence 필드를 다시 추가하거나
정책을 재설계하지 마세요.

## 필수 구현

### 1. production CLI

다음 명령을 구현해 주세요.

```bash
PYTHONPATH=src uv run python -m return_engine verify-input \
  --input-root <input-root> \
  --manifest-sha256 <sha256>

PYTHONPATH=src uv run python -m return_engine train-production \
  --input-root <input-root> \
  --manifest-sha256 <sha256> \
  --output-root <repository-root>/deploy/p1/seed/team-b
```

`train-production`은 입력 검증부터 학습·백테스트·exact-10·manifest export까지 한 번에
완료해야 합니다. 기존 output을 덮어쓰지 말고 새 폴더에만 성공해야 합니다.

### 2. 고정 학습 설정

```text
universe=Owner manifest의 고정 exact-31
priceBasis=RAW_CLOSE
minimumHistory=756 XKRX sessions
featureOrder=open,high,low,raw_close,volume,return_1d,ma5,ma20,rsi14
windowSize=20
hiddenSize=128
layerCount=3
dropout=0.2
outputSize=1
optimizer=Adam
learningRate=0.0005
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

- KIS·KRX·ECOS·yfinance 수집
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
2. input manifest SHA-256
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
