# Return Engine 마무리 부탁드립니다

안녕하세요. 지금까지 만들어 주신 LSTM, rule baseline, 데이터 처리, 백테스트 코드와 preview는 전부 그대로 두고 가겠습니다. 다시 쓰거나 지울 일은 없습니다. 현재 구현을 최대한 재사용하면서 production에 필요한 경계만 얹어 주시면 됩니다.

## 그대로 두시는 것

작업 범위는 `workspaces/return-engine/` 안쪽뿐입니다. 기존 preview, LSTM, rule baseline, preprocessing, backtest source와 테스트는 계속 통과하는 상태로 유지해 주세요.

legacy CSV와 PTH는 preview 전용 `LEGACY_RECEIVED_PREVIEW`로 남겨 두시면 됩니다. production artifact 쪽에는 PTH, pickle, joblib을 새로 만들지 말아 주세요. Spring이나 backend adapter, Dashboard, OCI signing·registry 도구는 Team B 몫이 아니니 신경 쓰지 않으셔도 됩니다.

시작하시기 전에 제가 로컬 receipt를 `workspaces/return-engine/dev/owner-handoff/<inputManifestSha256>/handoff.json`으로 전달드립니다. mode `0600` 일반 파일이고 symlink나 Git 추적은 안 됩니다. 안에는 `inputPackPath`, `inputPackManifestPath`, `inputPackManifestSha256`, `validatorCommand`, `providerCalls=0`만 들어 있습니다.

## 새로 붙여 주셔야 하는 것

- 제가 드리는 exact-31 input pack을 읽는 production train CLI, 그리고 accepted daily shard용 daily inference CLI
- manifest와 모든 input file의 size·SHA-256 선검증
- 종목별 train-only scaler, global time split 하나, leakage 0
- fixed ABI, round-trip 35bps 고정, final-test 재열람과 hyperparameter search 0
- exact-31 LSTM·rule 결과와 exact-10 artifact, 그리고 `p1-return-engine-manifest.v2.json`
- 같은 input·commit·lock·image로 두 번 돌렸을 때 manifest와 10개 파일이 byte 단위로 동일
- metric, split, scaler, cost의 독립 재계산
- production model은 `model.safetensors`로, 기존 PTH는 preview 전용으로

성과가 baseline에 못 미치면 숫자를 손보거나 추가 튜닝하지 마시고 `modelQuality=BELOW_BASELINE`을 그대로 공개해 주세요. 그게 훨씬 낫습니다. schema·semantic·exact-31·exact-10·two-run identity·독립 재계산·leakage 0이 다 통과하면 KIS Mock 후보로 쓸지는 제가 따로 판정하겠습니다. 실계좌나 수익, 성과 우월성에 대한 판단은 이 산출물의 역할이 아닙니다.

## 1.1.0 Automation과의 경계

이번에 제가 V91로 예산, 가변 수량, 손절·익절을 붙였습니다. 이건 전부 Decision Platform 쪽 책임이고 Team B가 뭔가 더 만들어 주셔야 하는 부분이 아닙니다.

Team B는 지금처럼 exact-31 Rule+LSTM 신호와 exact-10 artifact만 주시면 됩니다. 자금, 수량, LIMIT 가격, 손절·익절, 포지션 만기, RiskDecision, account·order·hash는 계산하지도 말고 새 필드로 추가하지도 말아 주세요. 신호는 후보 순위와 BUY·HOLD·SELL 근거까지만 담고, 주문 권한은 계속 없는 상태로 둡니다.

## 확인은 이렇게

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

exact-31과 exact-10, manifest v2의 schema·semantic, 두 실행 byte identity, metric·split·scaler·35bps 독립 재계산, network-none CPU Docker가 모두 통과하면 완료로 보겠습니다. exact-10은 `model.safetensors`, `scaler.json`, `config.json`, `lstm_signals.parquet`, `rule_baseline_signals.parquet`, `backtest_result.json`, `trade_log.parquet`, `equity_log.parquet`, `golden_output.json`, `model_report.md` 열 개입니다.

## 다 되면 알려 주세요

PR 주소와 commit SHA, `uv.lock`과 Dockerfile의 SHA-256, input manifest SHA-256, 결과 manifest SHA-256과 exact-10 hash 표, 두 실행 비교 결과, 그리고 unit·golden·독립 metric 결과를 같이 주시면 제가 이어서 확인하겠습니다.

## 이건 피해 주세요

- provider·KIS·ECOS·yfinance·Spring·account·order·Vertex·GDELT 호출이나 입력 보충
- Spring backend adapter, OCI packaging·SBOM·provenance·signature 도구 구현
- raw provider data, input pack, cache, 큰 output을 Git에 올리기
- 기존 preview 삭제, production PTH·pickle·joblib 출력, 성과 미화
- fixed ABI·35bps·split·leakage·exact count gate 완화
- 예산·수량·손절·익절·주문가격·포지션 관리 필드나 Spring API 추가

진행하면서 막히는 부분 있으면 편하게 물어봐 주세요.
