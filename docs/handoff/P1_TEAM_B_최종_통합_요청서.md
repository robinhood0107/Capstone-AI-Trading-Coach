# Team B 최종 요청서

안녕하세요. Return Engine은 이 문서 하나만 보고 진행해 주시면 됩니다.

## 한 줄 요청

현재 LSTM, rule baseline, 전처리와 백테스트를 유지하면서 exact-31 입력으로 재현 가능한 production
산출물과 거래일별 inference를 완성해 주세요.

뉴스, AI 판단, 자금·수량·손절·익절과 주문은 Owner가 처리합니다. Team B는 후보와
BUY·HOLD·SELL 신호까지만 담당합니다.

## 이번에 완성할 것

### 1. 한 번 실행하는 학습

- Owner가 준 input manifest의 파일 크기와 SHA-256 확인
- exact-31만 사용하고 종목별 train-only scaler 적용
- 모든 종목에 같은 시간 순서 split 사용
- leakage test, 왕복 거래비용 35bps, 고정 seed·thread 유지
- 같은 입력과 설정에서 같은 결과가 나오게 만들기

### 2. 거래일별 inference

학습을 매일 다시 하지 않고 고정된 `model.safetensors`와 `scaler.json`으로 그날 accepted daily shard만
처리해 주세요.

- 해당 `sessionDate`의 exact-31 LSTM 신호
- 같은 종목의 rule baseline 신호
- input·output manifest SHA-256
- 동일 입력 재실행 시 동일 bytes

날짜가 현재 거래 세션과 다르면 신호를 사용하지 않아야 합니다.

## 가능하면 해볼 제안: LSTM 성능 개선

필수 납품이 먼저 안정적으로 끝난 뒤 시간이 남는다면, 현재 LSTM의 성능을 먼저 측정하고 validation
구간 안에서 가능한 만큼 개선해봐도 좋습니다.

예를 들어 window, hidden size, dropout, learning rate, class imbalance 처리처럼 기존 구조 안에서 설명
가능한 항목을 소수만 비교해 주세요. 현재 모델과 개선 모델을 같은 split·비용으로 비교하고, 선택을
마친 뒤 final test는 한 번만 확인합니다. final test 결과를 보고 다시 튜닝하지 않습니다.

이 부분은 **제안이지 완료 조건이 아닙니다.** 성능이 좋아지지 않아도 괜찮으며, 낮은 결과도 그대로
기록해 주세요. 수익률이나 baseline 초과를 만들기 위해 데이터 경계나 비용을 바꾸면 안 됩니다.

## 제출할 production 묶음

한 번의 production 실행이 아래 exact-10과 manifest를 함께 만들면 됩니다.

| 파일 | 내용 |
|---|---|
| `model.safetensors` | LSTM 가중치 |
| `scaler.json` | 종목별 train-only scaler |
| `config.json` | feature 순서와 실행 설정 |
| `lstm_signals.parquet` | exact-31 LSTM 신호 |
| `rule_baseline_signals.parquet` | exact-31 rule 신호 |
| `backtest_result.json` | Baseline·Guide·Strict 결과 |
| `trade_log.parquet` | 거래 로그 |
| `equity_log.parquet` | 자산 곡선 |
| `golden_output.json` | 재현성 기준 |
| `model_report.md` | 데이터·split·성능·한계 |

같은 폴더의 `p1-return-engine-manifest.v2.json`에는 파일 크기와 SHA-256, input manifest, producer
commit·lock·설정 hash를 기록해 주세요.

```text
evidenceMode=REAL_TEAM_B
realTeamB=true
performanceClaimAllowed=false
orderAuthority=NONE
```

baseline보다 낮으면 `modelQuality=BELOW_BASELINE`을 그대로 기록하고 현재 validator 규칙에 따라
`mockRuntimeEligible=false`로 둡니다.

## 꼭 지켜 주세요

- 기존 preview, LSTM, rule, backtest와 테스트를 불필요하게 다시 작성하지 않음
- 학습·production 실행 중 외부 네트워크 0
- PTH·pickle·joblib을 production 산출물로 사용하지 않음
- Spring, Dashboard, DB, KIS·Vertex·계좌·주문 코드를 추가하지 않음
- raw input, cache와 큰 output을 Git에 올리지 않음

OCI packaging, SBOM, 서명, artifact import와 KIS Mock 연결은 Owner가 담당합니다.

## 완료 확인

```bash
cd workspaces/return-engine
uv sync --frozen
uv run pytest -q
uv run python -m return_engine --help
docker build --platform linux/amd64 -t capstone-return-engine:p1-local .
```

같은 input·commit·lock·image로 production 실행을 두 번 돌려 manifest와 exact-10 bytes가 같은지
확인해 주세요. leakage 0, 35bps 비용, split·scaler·metric 재계산도 함께 확인합니다.

완료 후에는 PR URL과 commit SHA, 두 실행의 manifest SHA-256, exact-10 hash 표, 테스트 결과,
daily inference 실행 명령만 보내 주세요. 별도 발표자료는 필요하지 않습니다.

## 구현 중 필요할 때만 보는 기술 참고

- [artifact manifest v2 schema](../../contracts/schemas/p1-return-engine-artifact-manifest.v2.schema.json)
- [exact-10 semantic schema 목록](../../contracts/catalogs/p1-owner-phase-a-contract-lock.v1.json)
