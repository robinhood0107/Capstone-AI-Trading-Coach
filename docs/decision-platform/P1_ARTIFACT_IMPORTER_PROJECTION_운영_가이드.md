# P1 artifact importer·projection 운영 가이드

## 현재 상태

```text
P1_OWNER_ARTIFACT_IMPORTER=VERIFIED_SYNTHETIC_MERGE_CANDIDATE
P1_OWNER_ARTIFACT_BUNDLE_SHA256=4f00835bfaba524fed50e0b41218d7a4d0af52fb6b66a9383053a88bf465107d
P1_OWNER_ARTIFACT_ARCHIVE=CONTENT_ADDRESSED_OWNER_PRIVATE
P1_OWNER_GOLDEN_SIGNAL_TEST_PROFILE=PASS
P1_OWNER_GOLDEN_PRODUCTION_POINTER=CLOSED_ALL_ABSTAIN
TEAM_B_REAL_ARTIFACT=PENDING_EXTERNAL_TEAM
PROVIDER_ACCOUNT_ORDER_CALLS=0
```

2026-09-01 current 경계는 V116과 artifact manifest v3다. V88·manifest v2·기존 synthetic receipt는
historical regression으로 보존한다.

```text
P1_CURRENT_ARTIFACT_SCHEMA=p1-return-engine-artifact-manifest.v3
P1_CURRENT_SIGNAL_CONFIDENCE_FIELDS=0
P1_GIT_MODEL_SEED_AUTO_IMPORT=IMPLEMENTED
P1_GIT_MODEL_SEED_PATH=deploy/p1/seed/team-b
P1_GIT_MODEL_SEED=BLOCKED_PENDING_TEAM_B_EXACT10
```

2026-08-27 실제 owner-private golden exact-10 bundle을 기본 5-container Compose의 V88 DB에 import했다.
첫 실행은 `IMPORTED`, 두 번째 실행은 `REPLAYED`와 `archiveNoOp=true`였고 provider call은 0이었다.
명시적 synthetic test profile에서 Signal, Model Evaluation, Backtest, ADMIN Ingest Status와 USER 403을
실제 localhost API로 확인했다. production 기본 profile로 재기동한 뒤 golden Signal은 all-ABSTAIN이었다.

## 실행

먼저 provider-free 기본 앱을 실행한다.

```bash
./capstone up

./capstone artifact import <owner-private-bundle-directory> \
  --manifest-sha256 <exact-manifest-sha256>
```

입력 directory는 current OS user가 소유한 absolute approved root 아래에 있어야 한다. importer는
manifest와 exact-10 file만 허용하고, 성공한 bytes는 Git 밖 state의 content-addressed archive에 새
directory로 보존한다. 기존 세대는 덮어쓰거나 삭제하지 않는다.

## 검증 순서

1. root와 모든 leaf의 owner/mode/regular-file/link count
2. exact inventory, size, SHA-256, semantic-schema mapping
3. config/scaler closed fields와 train-only exact-31
4. LSTM/rule signal formula, confidence 없는 closed field set, XKRX session
5. safetensors exact 31 namespace × 14 tensor, F32, shape, extent, finite
6. golden/input binding과 producer config/feature-order binding
7. trade/equity/backtest 독립 재계산
8. immutable archive publish
9. V116 `import_p1_return_bundle_v2` 한 transaction의 model seed/Dashboard/Ingest projection

`./capstone up`은 migration과 identity bootstrap 뒤 `deploy/p1/seed/team-b/`를 자동 검사한다. manifest가
없으면 `SKIPPED_SEED_ABSENT`, exact-10·hash·schema가 맞으면 첫 실행 `IMPORTED`, 같은 manifest 재실행은
`REPLAYED`다. 실패하면 Decision Platform persistent service를 시작하지 않는다.

같은 packet 재실행은 row를 늘리지 않는다. `runId`가 같고 bundle hash가 다르거나 validation/DB
transaction이 실패하면 production pointer는 바뀌지 않는다.

## synthetic와 real 경계

- `SYNTHETIC_GOLDEN`은 production Signal pointer가 아니다.
- synthetic Signal API 검증은 `P1_ARTIFACT_SYNTHETIC_TEST_PROFILE=true`인 로컬 E2E에서만 허용한다.
- 기본값은 `false`이며 synthetic만 있을 때 `GET /api/v2/signals/{symbol}`은 all-ABSTAIN이다.
- Dashboard projection도 `performanceClaimAllowed=false`를 유지한다.
- `REAL_TEAM_B`, `modelQuality=PASS | BELOW_BASELINE`,
  `mockRuntimeEligible=true`를 모두 만족한 검증 bundle만 production Signal
  pointer 후보가 된다. `BELOW_BASELINE`은
  수익 우월성을 주장하지 않는다는 공개 표식이며, input binding·leakage·재현성·35bps
  비용·metric 재계산을 통과한 bundle을 성능만으로 차단하지 않는다. 이 문서의
  synthetic 실측은 그 hard gate를 대체하지 않는다.

## 금지 경계

- provider, account, balance, order, Vertex, GDELT, KIS Live 호출
- raw bundle 또는 owner-private absolute path의 DB/Git 저장
- pickle/PTH/joblib/code-loading artifact
- synthetic 성과 주장, 자동 real 승격, LightGBM Signal 승격
- 기존 archive/DB/volume 삭제
