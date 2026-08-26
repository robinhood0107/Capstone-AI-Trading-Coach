# Team B 예측·백테스트 엔진 완료 요청

## 이번에 해주실 일

> 받은 소스, CSV, PTH와 JSON은 삭제하지 않고 미리보기 원본으로 보존했습니다. 이제 외부 인터넷 없이
> 한 번 실행하면 학습, 예측, 백테스트와 결과 저장까지 끝나는 Docker 작업을 만들고, 파일명과 개수가
> 고정된 결과 파일 10개와 검증 파일 하나를 만들어 주세요. Spring REST API와 주문 API는 호출하지
> 않습니다.

작업 위치는 `workspaces/return-engine/`입니다. 통합 PR이 main에 병합됐다는 안내를 받은 뒤 최신
main에서 작업해 주세요.

## 현재 코드와 완료 결과를 구분합니다

현재 Docker 실행은 받은 `005930.KS.csv`와 `005930.KS_lstm.pth`를 사용한 미리보기입니다. PTH의
SHA-256을 먼저 확인하고 `weights_only=True`로 읽으며 네트워크를 사용하지 않습니다. 결과에는
`LEGACY_RECEIVED_PREVIEW`, `realTeamB=false`가 붙습니다.

이 미리보기는 Team B 실제 완료 결과가 아닙니다. 현재 파일은 모두 남겨도 되지만 새 결과의 입력이나
학습 근거로 사용했다면 manifest에 정확히 기록해야 하며, 그렇지 않으면 실제 완료 결과로 표시하지
마세요.

## 시작 전에 통합 담당자가 제공해야 할 것

현재 저장소에는 아래 항목이 아직 하나의 실행 가능한 입력 계약으로 묶여 있지 않습니다. 통합 담당자가
이 항목을 제공하기 전에는 임의 값을 만들지 말고 `OWNER_INPUT_MISSING`으로 알려 주세요.

1. KIS 가격 자료의 파일명, schema, 기간, 종목, 해시가 적힌 입력 manifest
2. ECOS 거시 자료와 `contracts/schemas/ecos_macro_snapshot.schema.json`에 맞는 manifest
3. 뉴스 감성을 쓰는 경우에만 승인된 `news_sentiment_summary.v2` 입력
4. 각 시나리오의 수수료, 세금, slippage 값과 근거
5. 결과 manifest의 producer SHA-256 각 필드가 어떤 bytes를 해시하는지 적은 규칙

현재 결과 schema는 입력 전체를 `sourceSnapshotSha256` 하나로만 묶어 KIS·ECOS·조건부 뉴스를 각각
검증하지 못합니다. 별도 입력 manifest 또는 contract 변경이 먼저 필요합니다. Team B가 provider를
직접 호출해 이 빈 부분을 메우면 안 됩니다.

## 입력과 외부 호출 규칙

- 입력: 통합 담당자가 전달한 KIS 가격 snapshot/artifact
- 입력: 통합 담당자가 전달한 ECOS macro snapshot
- 선택 입력: 별도 승인된 `news_sentiment_summary.v2`
- `yfinance`, KIS, ECOS 등 외부 서비스 직접 호출: 0회
- 계좌, 잔고, 주문, credential 접근: 0회
- Spring REST API 호출: 0회

기존 `yfinance` 코드와 받은 원본 파일은 보존해도 되지만, 최종 Docker 실행 경로에서는 네트워크가
차단된 상태로 동작해야 합니다.

## 한 번 실행할 때 수행할 단계

1. 입력 manifest와 모든 입력 파일의 크기·SHA-256 확인
2. feature 순서와 결측 처리 고정
3. 고정 seed로 LSTM 학습 또는 승인된 모델 변환
4. 다음 XKRX 거래일 예측과 규칙 baseline 생성
5. 비용을 반영한 backtest 실행
6. 거래 로그, 자산 곡선, 모델 보고서 생성
7. 결과 파일 10개를 임시 폴더에서 모두 검증
8. 검증 파일을 마지막에 쓰고 결과 폴더로 한 번에 게시

Python 3.12, PyTorch 2.13.0 CPU와 `uv.lock`을 유지합니다. 다음 예측일은 고정된 XKRX calendar의
다음 session을 사용하고, 예상 수익률은 `forecastClose / currentClose - 1`로 계산합니다.

`BASELINE`, `GUIDE`, `STRICT`는 백테스트 비용 시나리오 이름입니다. Team B가 투자 원칙이나
RiskEngine을 새로 구현하는 뜻이 아닙니다. 통합 담당자가 제공한 시나리오 설정을 그대로 적용하고,
설정이 없으면 계산하지 않습니다. 공통 지표 정의는 `shared-docs/backtest_config.yaml`과
`shared-docs/metrics_definitions.md`를 따릅니다.

## 결과 폴더와 파일 10개

결과는 `artifacts/return-engine/<runId>/` 아래에 둡니다.

1. `model.safetensors`
2. `scaler.json`
3. `config.json`
4. `lstm_signals.parquet`
5. `rule_baseline_signals.parquet`
6. `backtest_result.json`
7. `trade_log.parquet`
8. `equity_log.parquet`
9. `golden_output.json`
10. `model_report.md`

같은 폴더에 `p1-return-engine-manifest.v1.json`을 둡니다. 이번 완료 판정에는 구형 generic schema가
아니라 아래 파일만 사용합니다.

```text
contracts/schemas/p1-return-engine-artifact-manifest.v1.schema.json
```

manifest에는 다음을 모두 넣습니다.

- `contractId=p1-return-engine-artifact-manifest.v1`
- `evidenceMode=REAL_TEAM_B`
- 10개 결과의 상대경로, 파일 크기, SHA-256
- 다음 XKRX session, 현재 종가, 예측 종가, 예상 수익률
- 세 시나리오의 수수료, 세금, slippage bps
- `seed`, `windowSessions`
- `commitSha256`, `dependencyLockSha256`, `dockerfileSha256`
- `sourceSnapshotSha256`, `trainingCodeSha256`, `featureOrderSha256`
- `splitSha256`, `configSha256`, `goldenOutputSha256`

각 SHA-256은 통합 담당자가 정한 canonical bytes를 사용합니다. 단순히 임의 문자열을 해시해 채우면
안 됩니다.

## 재현성 기준

동일한 입력, commit, lockfile과 Docker image로 두 번 실행했을 때 manifest와 결과 파일 10개의
SHA-256이 전부 같아야 합니다. 현재 계약에는 허용오차가 정의되어 있지 않으므로 “비슷하다”는 판정은
사용하지 않습니다. byte 일치가 불가능한 항목이 있으면 Team B가 임의 허용오차를 정하지 말고
contract 변경 제안으로 분리해 주세요.

## 실행·검증 예시

최종 CLI는 입력과 출력 위치를 명시적으로 받도록 구현해 주세요. 아래는 요구되는 사용 형태입니다.

```bash
cd workspaces/return-engine
uv sync --frozen
uv run --frozen pytest
docker build --platform linux/amd64 -t capstone-return-engine:p1-local .

mkdir -p ../../artifacts/return-engine/run-1 ../../artifacts/return-engine/run-2
docker run --rm --network none \
  --mount type=bind,src=<owner-input-dir>,dst=/input,readonly \
  --mount type=bind,src="$PWD/../../artifacts/return-engine/run-1",dst=/output \
  capstone-return-engine:p1-local run --input /input/manifest.json --output /output
docker run --rm --network none \
  --mount type=bind,src=<owner-input-dir>,dst=/input,readonly \
  --mount type=bind,src="$PWD/../../artifacts/return-engine/run-2",dst=/output \
  capstone-return-engine:p1-local run --input /input/manifest.json --output /output
```

저장소 루트에서 두 결과의 SHA-256을 비교하고 검증기를 실행합니다.

```bash
python3 contracts/verify_p1_full_app_assets.py \
  --return-manifest artifacts/return-engine/run-1/p1-return-engine-manifest.v1.json
```

## 통합 담당자가 나중에 할 일

Team B가 직접 API에 넣지 않습니다. 통합 담당자가 결과 변환·적재 코드를 별도로 완성한 뒤 다음 API에서
실제 결과를 확인합니다. 이 변환·적재 경로는 현재 아직 구현되지 않았습니다.

- `GET /api/v2/signals/{symbol}`
- `GET /api/v1/dashboard/model-evaluations/{runId}`
- `GET /api/v1/dashboard/backtests/{runId}`
- `GET /api/v1/artifacts/ingest-status`

## 보내 주실 것

1. PR 주소와 최신 commit SHA
2. `uv.lock`과 Dockerfile SHA-256
3. 사용한 입력 manifest 경로와 SHA-256
4. 결과 manifest 경로와 SHA-256
5. 두 번 실행한 결과 11개 파일의 SHA-256 비교표
6. unit/golden test와 network-disabled Docker 실행 결과
7. `OWNER_INPUT_MISSING` 또는 필요한 contract 변경 목록

## 그대로 보내는 짧은 메시지

```text
통합 PR이 main에 병합됐다는 안내를 받은 뒤 최신 main을 받아 주세요.
workspaces/return-engine에서 외부 서비스나 Spring API를 호출하지 않는 Docker 작업을 완성해 주세요.
한 번 실행하면 입력 검증, 학습, 예측, 백테스트와 고정된 결과 파일 10개 생성까지 끝나야 합니다.
현재 받은 CSV/PTH/소스/JSON은 삭제하지 않습니다. 입력 계약이나 비용값이 없으면 임의로 만들지 말고
OWNER_INPUT_MISSING으로 알려 주세요. 완료 조건과 제출물은 이 요청서를 그대로 따라 주세요.
```
