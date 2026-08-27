# Team B Return Engine 완료 요청

지금까지 만든 LSTM, rule baseline, 데이터 처리, 백테스트 코드와 기존 preview를 그대로 보존해 주세요. 전면 재작성이나 삭제는 요청하지 않습니다. 현재 구현을 최대한 재사용하면서 production에 필요한 경계만 추가해 주세요.

## 1. 기존 작업 중 보존할 것

- 수정 범위는 `workspaces/return-engine/`뿐입니다. 기존 preview, LSTM, rule baseline, preprocessing, backtest source와 기존 테스트를 계속 PASS 상태로 유지합니다.
- legacy CSV/PTH는 preview 전용 `LEGACY_RECEIVED_PREVIEW`로 보존합니다. production artifact에는 PTH, pickle, joblib을 새로 만들지 않습니다.
- Team B는 Spring/backend adapter, Dashboard, OCI signing·registry 도구를 구현하지 않습니다.

작업 전에 Owner가 ignored local receipt `workspaces/return-engine/dev/owner-handoff/<inputManifestSha256>/handoff.json`을 제공합니다. receipt는 mode `0600` regular file이고 symlink·Git 추적을 금지하며 `inputPackPath`, `inputPackManifestPath`, `inputPackManifestSha256`, `validatorCommand`, `providerCalls=0`만 포함합니다.

## 2. 추가할 production 기능

- Owner exact-31 input pack을 읽는 production train CLI와 accepted daily shard용 daily inference CLI
- manifest와 모든 input file size/SHA-256 선검증
- 종목별 train-only scaler, 하나의 global time split, leakage 0
- fixed ABI, fixed round-trip 35bps, final-test 재열람·hyperparameter search 0
- exact-31 LSTM/rule 결과, exact-10 artifact와 `p1-return-engine-manifest.v2.json`
- 동일 input/commit/lock/image 두 실행의 manifest+10개 파일 byte identity
- independent metric, split, scaler, cost 재계산
- production model `model.safetensors`; 기존 PTH는 preview 전용

성과가 baseline 미만이면 수치를 꾸미거나 추가 튜닝하지 말고 `modelQuality=BELOW_BASELINE`을 그대로 공개합니다. schema·semantic·exact-31/exact-10·two-run identity·independent recomputation·leakage 0가 PASS하면 Owner가 KIS Mock 후보 여부를 별도 판정합니다. 실계좌·수익·성과 우월 주장 권한은 없습니다.

### 1.1.0 Automation 경계

V91의 예산, 가변수량, 손절·익절은 전부 Owner Decision Platform 책임이며 Team B 추가 산출물이
아닙니다. Team B는 기존 exact-31 Rule+LSTM 신호와 exact-10 artifact만 제공합니다. 자금, 수량,
LIMIT 가격, 손절·익절, 포지션 만기, RiskDecision, account/order/hash를 계산하거나 새 필드로 추가하지
마세요. Team B 신호는 후보 순위와 BUY/HOLD/SELL 근거만 가지며 주문 권한은 계속 0입니다.

## 3. 실행 명령

```bash
cd workspaces/return-engine
uv sync --frozen
uv run --frozen pytest -q
docker build --platform linux/amd64 -t capstone-return-engine:p1-local .

docker run --rm --network none \
  --mount type=bind,src=<owner-input-dir>,dst=/input,readonly \
  --mount type=bind,src=<run-1-output>,dst=/output \
  capstone-return-engine:p1-local run --input /input/manifest.json --output /output

./capstone artifact validate <run-1-output> --manifest-sha256 <manifest-sha256>
./capstone artifact validate <run-2-output> --manifest-sha256 <manifest-sha256>
```

## 4. 완료 기준과 제출물

완료 기준은 exact-31/exact-10, manifest v2 schema·semantic, two-run byte identity, independent metric/split/scaler/35bps, network-none CPU Docker가 모두 PASS하는 것입니다. exact-10은 `model.safetensors`, `scaler.json`, `config.json`, `lstm_signals.parquet`, `rule_baseline_signals.parquet`, `backtest_result.json`, `trade_log.parquet`, `equity_log.parquet`, `golden_output.json`, `model_report.md`입니다.

제출물은 PR URL·commit SHA, `uv.lock`·Dockerfile SHA-256, input manifest SHA-256, 결과 manifest SHA-256·exact-10 hash 표, 두 실행 비교와 unit/golden/independent metric 결과입니다.

## 5. 하지 말아야 할 것

- provider/KIS/ECOS/yfinance/Spring/account/order/Vertex/GDELT 호출 또는 입력 보충
- Spring backend adapter와 OCI packaging·SBOM·provenance·signature 도구 구현
- raw provider data, input pack, cache, 대용량 output을 Git에 추가
- 기존 preview 삭제, production PTH/pickle/joblib 출력, 성과 미화
- fixed ABI·35bps·split·leakage·exact count gate 완화
- 예산·수량·손절·익절·주문가격·포지션 관리 필드나 Spring API 추가
