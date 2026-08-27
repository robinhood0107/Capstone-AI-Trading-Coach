# Team B 예측·백테스트 엔진 완료 요청

## 결론부터

`OWNER_INPUT_MISSING`은 해소됐습니다. Owner가 exact-31 입력 pack, 고정 ABI·비용·split, semantic schema,
synthetic golden, 실제 결과 validator/importer, Spring projection과 inference runtime을 이미 준비했습니다.
Team B는 provider, Spring, Dashboard, 계좌, 주문, Vertex/GDELT를 구현하지 않습니다.

남은 역할은 `workspaces/return-engine/` 안에서 Owner 입력만 읽는 실제 price-only LSTM 실행 경로와
exact-10 결과를 만드는 것입니다. 기존 CSV/PTH preview와 지금까지 작성한 소스는 삭제하거나 전면
재작성하지 말고 `LEGACY_RECEIVED_PREVIEW`로 보존한 채 새 real 실행 경로를 분리해 주세요.

## Owner가 이미 끝낸 것

- `p1-return-engine-input-pack.v1` exact-31 sealed input과 manifest SHA-256
- feature order `open, high, low, raw_close, volume, return_1d, ma5, ma20, rsi14`
- window 20, hidden 128, 3-layer LSTM, seed 0, CPU thread 1의 fixed ABI
- train/validation/test global split, XKRX calendar, fixed round-trip 35bps
- exact-10 semantic schema와 `p1-return-engine-artifact-manifest.v2`
- synthetic golden과 byte-determinism oracle
- hostile-input-safe local validator/importer, Signal·model evaluation·backtest projection
- loopback bounded production inference process

Owner는 작업 시작 시 input pack의 읽기 전용 경로와 manifest SHA-256을 별도 전달합니다. Team B가 KIS,
ECOS, yfinance 또는 뉴스로 입력을 보충할 필요가 없습니다. macro snapshot은 pack의 provenance일 뿐 LSTM
feature가 아니며 뉴스 feature와 GDELT input은 정확히 0입니다.

## Team B가 실제로 구현할 것

1. input manifest와 모든 파일의 size/SHA-256을 먼저 검증
2. 종목별 train-only scaler와 하나의 global time split 사용
3. exact-31 각각에 같은 fixed price-only 3-layer LSTM과 rule baseline 실행
4. leakage 0, final-test 재열람 0, hyperparameter search 0 유지
5. `BASELINE`, `GUIDE`, `STRICT` replay에 Owner fixed 35bps를 동일 적용
6. next XKRX session 예측과 `forecastClose / currentClose - 1` 재계산
7. exact-10을 임시 directory에서 모두 검증한 뒤 manifest를 마지막에 게시
8. 동일 input/commit/lock/image 두 실행의 manifest+10개 파일 byte identity 증명

실제 성능이 계약 기준을 통과하면 `modelQuality=PASS`, `mockRuntimeEligible=true`를 사용합니다. 통과하지
못하면 숫자를 꾸미거나 추가 튜닝하지 말고 `BELOW_BASELINE`과 비활성 상태로 제출합니다. 어느 경우든
`furtherTuningRequired=false`, `orderAuthority=NONE`, `performanceClaimAllowed=false`입니다.

## 결과 폴더와 파일 10개

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

같은 directory의 manifest 이름은 `p1-return-engine-manifest.v2.json`, contract ID는
`p1-return-engine-artifact-manifest.v2`입니다. pickle, joblib 또는 PTH를 production artifact로 새로
내보내지 않습니다. 기존 PTH preview는 historical input으로만 남깁니다.

## 실행과 Owner validator

Team B가 제공할 CLI는 입력과 출력을 명시적으로 받는 one-shot이어야 합니다.

```bash
cd workspaces/return-engine
uv sync --frozen
uv run --frozen pytest -q
docker build --platform linux/amd64 -t capstone-return-engine:p1-local .

docker run --rm --network none \
  --mount type=bind,src=<owner-input-dir>,dst=/input,readonly \
  --mount type=bind,src=<run-1-output>,dst=/output \
  capstone-return-engine:p1-local run --input /input/manifest.json --output /output

docker run --rm --network none \
  --mount type=bind,src=<owner-input-dir>,dst=/input,readonly \
  --mount type=bind,src=<run-2-output>,dst=/output \
  capstone-return-engine:p1-local run --input /input/manifest.json --output /output
```

Owner validator는 별도 DB, provider, account 또는 order 호출 없이 network-none container에서 실행됩니다.

```bash
./capstone artifact validate <run-1-output> --manifest-sha256 <manifest-sha256>
./capstone artifact validate <run-2-output> --manifest-sha256 <manifest-sha256>
```

Team B는 validator나 Spring adapter를 수정하지 않습니다. 실패하면 정확한 artifact/manifest를 고쳐 다시
실행하면 됩니다.

## 통합 담당자가 나중에 할 일

Team B가 직접 API에 넣거나 backend adapter를 작성하지 않습니다. Owner가 받은 bytes를 검증·archive·import한
뒤 아래 기존 API를 같은 `runId`와 source hash로 확인합니다.

- `GET /api/v2/signals/{symbol}`
- `GET /api/v1/dashboard/model-evaluations/{runId}`
- `GET /api/v1/dashboard/backtests/{runId}`
- `GET /api/v1/artifacts/ingest-status`

restricted GHCR packaging, SBOM, provenance, signature와 OCI digest 검증도 Owner supply-chain lane에서
담당합니다. Team B가 이미 재현 가능한 OCI를 만들었다면 digest를 함께 보내도 되지만, Team B core 완료를
위해 signing/registry 도구를 새로 구현할 필요는 없습니다.

## 완료 확인

- exact-31과 exact-10, manifest v2 schema/semantic PASS
- two-run manifest+10 files byte identity
- independent metric, split, scaler, 35bps 재계산 PASS
- `--network none` CPU Docker PASS
- provider/Spring/account/order/GDELT/Vertex call 0
- production pickle/joblib/PTH 0
- input manifest SHA와 source/lock/Dockerfile/output hash binding PASS

## 보내 주실 것

1. PR URL과 commit SHA
2. `uv.lock`과 Dockerfile SHA-256
3. 사용한 input manifest SHA-256
4. 결과 manifest SHA-256과 exact-10 hash 표
5. 두 실행 byte 비교와 unit/golden/independent metric 결과

OCI/SBOM/provenance/signature는 Owner가 맡습니다. input 자체, raw provider data, local cache와 대용량 실행
output은 Git에 올리지 않습니다.

## 그대로 보내는 짧은 메시지

```text
최신 main에서 workspaces/return-engine만 수정해 주세요. Owner가 exact-31 input, fixed ABI·35bps·split,
exact-10/manifest v2 schema, golden, validator/importer와 API projection을 모두 준비했으므로 provider,
Spring, OCI 서명 도구를 만들 필요가 없습니다. 기존 preview는 보존하고 새 price-only LSTM one-shot을
network-none에서 두 번 실행해 exact-10 byte identity와 metric 재계산을 증명한 뒤 PR·lock·manifest/hash
결과만 보내 주세요.
```
