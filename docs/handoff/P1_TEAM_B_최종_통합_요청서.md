# Team B 최종 요청서

안녕하세요. 이 문서 하나만 보시고 Return Engine 작업을 진행해 주시면 됩니다.
Automation V3가 추가됐지만 Team B의 모델·신호 계약은 바뀌지 않았습니다.

## 이번에 부탁드리는 한 가지

지금까지 만든 preview, LSTM, rule baseline, 전처리와 백테스트를 그대로 유지하면서, Owner가 전달한
exact-31 가격 입력을 읽어 재현 가능한 production 산출물을 만들어 주세요.

Team B의 결과는 “후보와 BUY·HOLD·SELL 신호”까지만 담당합니다. 뉴스, AI 판단, 자금, 수량,
손절·익절과 주문은 Owner의 Decision Platform이 처리합니다.

## 그대로 살려 주세요

- 현재 `workspaces/return-engine/`의 preview와 모델 코드
- LSTM과 rule baseline
- 기존 전처리와 백테스트
- legacy CSV·PTH preview
- 기존 테스트

기존 코드를 다시 쓰거나 다른 모델을 새로 도입할 필요는 없습니다. 모델 성능을 높이기 위한 추가 탐색도
이번 요청에 포함하지 않습니다.

## 완성할 실행 두 가지

### 1. 한 번 실행하는 학습

Owner가 전달한 input pack과 manifest SHA-256을 그대로 사용합니다.

- exact-31 종목만 읽기
- 모든 입력 파일의 크기와 SHA-256을 먼저 확인
- 종목별 train-only scaler 사용
- 전체 종목에 공통인 시간 순서 split 사용
- 미래 데이터가 학습에 섞이지 않는 leakage test
- 거래비용 왕복 35bps 고정
- seed와 thread 수를 고정해 같은 입력에서 같은 결과 생성

평가 결과를 본 뒤 final test를 다시 열어 튜닝하지 마세요.

### 2. 거래일마다 실행하는 inference

학습은 매일 다시 하지 않습니다. 고정된 `model.safetensors`와 `scaler.json`으로 그날 accepted daily
shard만 추론합니다.

- 당일 `sessionDate`의 exact-31 LSTM 신호
- 같은 종목의 rule baseline 신호
- input·output manifest SHA-256
- 같은 입력으로 다시 실행했을 때 같은 bytes

날짜가 당일 거래 세션과 다르면 자동운용은 신호를 사용하지 않습니다.

## 제출할 exact-10

아래 파일 이름은 정확히 유지해 주세요.

| 파일 | 내용 |
|---|---|
| `model.safetensors` | production LSTM 가중치 |
| `scaler.json` | 종목별 train-only scaler |
| `config.json` | feature 순서와 고정 실행 설정 |
| `lstm_signals.parquet` | exact-31 LSTM 신호 |
| `rule_baseline_signals.parquet` | exact-31 규칙 신호 |
| `backtest_result.json` | Baseline·Guide·Strict 결과 |
| `trade_log.parquet` | 거래 로그 |
| `equity_log.parquet` | 자산 곡선 |
| `golden_output.json` | 재현성 비교 기준 |
| `model_report.md` | 데이터·split·성능·한계 설명 |

같은 폴더에 `p1-return-engine-manifest.v2.json`을 둡니다. manifest에는 각 파일의 크기와 SHA-256,
input pack SHA-256, producer commit·lock·설정 hash를 기록합니다.

반드시 유지할 표기:

```text
evidenceMode=REAL_TEAM_B
realTeamB=true
performanceClaimAllowed=false
orderAuthority=NONE
```

모델이 baseline보다 낮으면 `modelQuality=BELOW_BASELINE`을 그대로 기록합니다. 현재 validator 기준으로
이때 `mockRuntimeEligible=false`여야 합니다. 숫자를 좋게 보이게 바꾸거나 추가 튜닝하지 마세요.

## Team B가 하지 않는 일

- 뉴스·citation·Grounding·Vertex·GDELT 기능 추가
- ATR, peak, trailing stop 계산
- 예산, 수량, LIMIT 가격, 손절·익절, 보유 만기 계산
- RiskDecision, account, order, intent hash 생성
- KIS·KRX·ECOS·yfinance 등 외부 네트워크 호출로 입력 보충
- Spring API, Dashboard, DB migration, Compose 수정
- OCI registry 게시, SBOM·provenance·서명 도구 구현
- PTH·pickle·joblib을 production 산출물로 추가
- 기존 preview 삭제

OCI packaging, SBOM, 서명, artifact import와 실제 KIS Mock 연결은 Owner가 맡습니다.

## 완료 확인

```bash
cd workspaces/return-engine
uv sync --frozen
uv run pytest -q
uv run python -m return_engine --help

docker build --platform linux/amd64 -t capstone-return-engine:p1-local .
```

그다음 동일한 input·commit·lock·image로 production 실행을 두 번 수행합니다.

완료 기준:

- exact-31 입력과 신호
- exact-10 파일과 manifest v2
- 두 실행의 manifest와 10개 파일 byte identity
- split·scaler·metric·35bps 비용 독립 재계산
- leakage 0
- production 실행 network 0
- Spring·provider·account·order 호출 0
- daily inference 재실행 명령과 sample manifest 준비

모델의 수익률이나 baseline 초과는 완료 조건이 아닙니다. 낮은 성능도 정확하게 기록하면 됩니다.

## 완료 후 보내 주세요

- PR URL과 commit SHA
- `uv.lock`과 Dockerfile SHA-256
- input manifest와 output manifest SHA-256
- exact-10 파일 hash 표
- 두 실행 비교 결과
- unit·golden·독립 metric·leakage 테스트 결과
- daily inference 실행 명령과 sample manifest

큰 output, input pack, cache와 모델 원본은 Git에 올리지 마세요. Owner가 별도 intake 경로를 안내합니다.

## 기술 참고

구현 중 파일 형식이 필요할 때만 아래 계약을 보시면 됩니다. 별도의 추가 요청서는 아닙니다.

- [artifact manifest v2 schema](../../contracts/schemas/p1-return-engine-artifact-manifest.v2.schema.json)
- [exact-10 semantic schema 목록](../../contracts/catalogs/p1-owner-phase-a-contract-lock.v1.json)
