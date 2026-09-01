# Team B 최종 요청서

안녕하세요. Return Engine은 이 문서 하나만 보고 진행해 주시면 됩니다.

## 한 줄 요청

현재 LSTM, rule baseline, 전처리와 백테스트를 유지하면서 exact-31 입력으로 재현 가능한 production
산출물과 거래일별 inference를 완성해 주세요.

뉴스, AI 판단, 자금·수량·손절·익절과 주문은 Owner가 처리합니다. Team B는 후보와
BUY·HOLD·SELL 신호까지만 담당합니다.

## 이전 세션 없이 시작하는 방법

이전에 진행하던 대화나 개발 세션이 없다는 전제로 시작해도 됩니다. 과거 진행 내용을 복원하려고
할 필요 없이 현재 `main`과 아래 순서만 따라가면 됩니다.

1. 이 요청서를 끝까지 한 번 읽습니다.
2. [`workspaces/return-engine/README.md`](../../workspaces/return-engine/README.md) 상단의 현재 통합 상태와
   기존 코드 구조를 확인합니다.
3. [Owner input pack 운영 가이드](../decision-platform/P1_OWNER_INPUT_PACK_GOLDEN_운영_가이드.md)에서
   입력이 어떻게 봉인되는지만 확인합니다. pack 생성은 Owner가 합니다.
4. [`p1-owner-phase-a-contract-lock.v1`](../../contracts/catalogs/p1-owner-phase-a-contract-lock.v1.json)에서
   exact-10 파일명과 feature 순서를 확인합니다.
5. [input pack schema](../../contracts/schemas/p1-return-engine-input-pack.v1.schema.json)와
   [artifact manifest v2 schema](../../contracts/schemas/p1-return-engine-artifact-manifest.v2.schema.json)를
   실제 입출력의 기준으로 사용합니다.

Owner에게 처음 받을 것은 딱 두 가지입니다.

```text
1. owner-private input pack root
2. manifest.json SHA-256
```

두 값이 없으면 임의 CSV나 yfinance로 production 실행을 대신하지 말고, 기존 preview 코드와 테스트만
정리해 두고 입력을 기다리면 됩니다. Decision Platform 전체 구조를 이해해야 시작할 필요는 없습니다.

### 현재 코드에서 무엇을 참고하면 되는가

| 현재 파일 | 활용 방법 |
|---|---|
| `src/models/lstm.py` | 기존 LSTM 구조를 출발점으로 재사용 가능 |
| `src/models/rule_baseline.py` | rule signal 계산을 재사용하고 exact-31 출력으로 확장 |
| `src/backtest_core/` | 비용 35bps와 공통 split을 적용하도록 보완해 재사용 |
| `src/dataloader/` | feature 계산 아이디어는 재사용하되 production 입력을 sealed pack reader로 교체 |
| `src/preview_cli.py`, `preview_contract.py` | 실행·검증 방식 참고용. production artifact producer로 승격하지 않음 |
| `data/model/*.pth`, `data/stock/*.csv` | 역사적 preview 입력. exact-10에 복사하거나 REAL_TEAM_B 근거로 사용하지 않음 |
| `workspaces/decision-platform/python-services/tests/e2e/team_b_bundle.py` | Owner의 test-only bundle shape 참고용. 실행 결과를 실제 Team B 결과로 제출하지 않음 |

권장 구현 순서는 `input 검증 → production train → exact-10 export → 두 번 실행 byte 비교 → daily
inference`입니다. 모델 성능 개선부터 시작하면 계약·재현성 작업이 뒤로 밀리므로 마지막에 둡니다.

CLI 이름은 내부에서 정해도 되지만 다음처럼 역할이 분리되면 Owner가 연결하기 쉽습니다.

```text
train-production --input-root ... --manifest-sha256 ... --output-root ...
infer-daily --bundle-root ... --daily-shard ... --output-root ...
```

## 현재 부족한 것 — 아래 4개가 이번 필수 작업의 전부입니다

현재 저장소에는 005930 preview, 기존 LSTM·rule·backtest 코드가 있지만 결과는
`LEGACY_RECEIVED_PREVIEW`, `realTeamB=false`입니다. 실제 자동운용에 넣을 production 산출물은 아직
아닙니다. 반드시 채워야 하는 부분은 다음 네 가지입니다.

1. **exact-31 production 학습:** Owner input manifest를 검증하고 전체 종목을 같은 시간 경계로 학습하는 실행 경로
2. **검증 가능한 모델:** train-only scaler, leakage test, 35bps 비용, 고정 seed·thread와 성능 측정
3. **실제 exact-10:** `model.safetensors`를 포함한 10개 파일과 `p1-return-engine-manifest.v2.json`
4. **daily inference:** accepted daily shard를 읽어 그 거래일의 exact-31 LSTM·rule 신호를 만드는 명령과 manifest

위 4개가 모두 있어야 `REAL_TEAM_B`로 전달할 수 있습니다. LSTM 성능 개선은 이 필수 작업을 완료한
뒤의 선택 제안입니다.

## 필수 구현 내용

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

baseline보다 낮으면 `modelQuality=BELOW_BASELINE`을 그대로 기록합니다. 성능을 숨기지 않는 대신
input binding, leakage 0, 재현성, 35bps 비용과 독립 metric 재계산이 통과하면
`mockRuntimeEligible=true`로 둡니다. 이는 모의운용 적격성일 뿐 수익·우월성 주장이 아닙니다.

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
PYTHONPATH=src uv run python -m return_engine --help
docker build --platform linux/amd64 -t capstone-return-engine:p1-local .
```

같은 input·commit·lock·image로 production 실행을 두 번 돌려 manifest와 exact-10 bytes가 같은지
확인해 주세요. leakage 0, 35bps 비용, split·scaler·metric 재계산도 함께 확인합니다.

완료 후에는 PR URL과 commit SHA, 두 실행의 manifest SHA-256, exact-10 hash 표, 테스트 결과,
daily inference 실행 명령만 보내 주세요. 별도 발표자료는 필요하지 않습니다.

## 제출 뒤에는 어떻게 연결되는가

Team B는 DB나 자동매매를 직접 켜지 않습니다. bundle과 manifest SHA를 보내면 Owner가 아래 순서로
검증·적재·활성화합니다.

```bash
./capstone artifact validate <team-b-bundle> --manifest-sha256 <sha256>
./capstone artifact import <team-b-bundle> --manifest-sha256 <sha256>
./capstone up --mock
./capstone mock gate-author
./capstone mock readiness
./capstone mock start
```

실제 bundle이 정상이라면 `REAL_TEAM_B_POINTER`, release/source binding, certification과 account baseline은
Owner gate가 다시 계산합니다. Team B가 이 값들을 manifest에 임의로 넣거나 DB를 수정할 필요가 없습니다.
막히면 마지막 명령의 blocker 이름과 manifest SHA만 Owner에게 전달해 주세요.

## 구현 중 필요할 때만 보는 기술 참고

- [artifact manifest v2 schema](../../contracts/schemas/p1-return-engine-artifact-manifest.v2.schema.json)
- [exact-10 semantic schema 목록](../../contracts/catalogs/p1-owner-phase-a-contract-lock.v1.json)
- [valid input pack 예시](../../contracts/examples/p1-return-engine-input-pack.v1.valid.json)
