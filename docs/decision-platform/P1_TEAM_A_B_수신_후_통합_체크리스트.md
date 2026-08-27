# 두 팀 결과를 받은 뒤 통합 확인표

## 0. 현재 Owner 전제

- [x] root OpenAPI exact-56과 Automation/Journal API
- [x] Team A exact-33 catalog, generated client, Seed/reset, live Spring acceptance
- [x] Team B exact-31 input, exact-10/manifest v2 schema, golden, validator/importer/projection/inference
- [x] 기본 5·models 7 Compose, ordinary security, supply-chain intake, handoff
- [x] Team 결과 수신 뒤 필요한 Owner backend adapter 0
- [x] provider/account/order physical call 0

Team A/B는 위 Owner 작업을 다시 구현하지 않습니다. Team 결과가 없으면
`TEAM_A_REAL_UI=PENDING_EXTERNAL_TEAM`, `TEAM_B_REAL_ARTIFACT=PENDING_EXTERNAL_TEAM`에서 정상적으로
멈춥니다.

## 1. Team A 수신 확인

- [ ] diff가 `workspaces/experience-dashboard/`로 제한됨
- [ ] PR URL, commit SHA, `package-lock.json` SHA-256 확인
- [ ] 기존 화면을 보존한 로그인·근거, 모의주문, automation, RAG·Journal, truth badge 흐름 확인
- [ ] typecheck, lint, unit, contract, build, UI Playwright PASS와 skip 0
- [ ] frontend fake production response 0, backend/OpenAPI/migration/Compose diff 0
- [ ] KIS_MOCK/INTERNAL_PAPER, synthetic/REAL_TEAM_B, LightGBM RESEARCH_ONLY 표시 분리
- [ ] credential, JWT, password, API 원문 report 노출 0

Team A가 수동 exact-33 표나 production image digest를 만들지 않았다는 이유만으로 실패시키지 않습니다.
Owner가 수신 commit에서 image를 build하고 `./capstone team-a acceptance`로 exact-33을 다시 증명합니다.

## 2. Team B 수신 확인

- [ ] diff가 `workspaces/return-engine/`로 제한되고 기존 preview가 보존됨
- [ ] PR URL, commit SHA, `uv.lock`, Dockerfile SHA-256 확인
- [ ] Owner가 전달한 exact-31 input manifest SHA와 결과 manifest binding 일치
- [ ] price-only fixed 3-layer LSTM, rule baseline, train-only scaler, global split, leakage 0
- [ ] fixed 35bps, exact-10, manifest v2, `furtherTuningRequired=false`
- [ ] 두 network-none CPU 실행의 manifest+10 files byte identity
- [ ] independent metric·split·예상수익률 재계산 PASS
- [ ] provider/Spring/account/order/GDELT/Vertex call 0
- [ ] production pickle/joblib/PTH 0

먼저 두 결과를 local Owner validator로 검사합니다.

```bash
./capstone artifact validate <run-1-output> --manifest-sha256 <manifest-sha256>
./capstone artifact validate <run-2-output> --manifest-sha256 <manifest-sha256>
```

Team B가 OCI signing 도구를 구현하지 않았다는 이유만으로 model core를 실패시키지 않습니다. Owner가 통과한
source와 exact-10으로 restricted OCI, SBOM, provenance, signature를 재현하고 immutable digest를 검증합니다.

## 3. 무코딩 통합

Team A/B 계약이 모두 통과할 때만 다음을 수행합니다.

1. Team B exact-10을 hostile-input-safe importer로 atomic import
2. REAL_TEAM_B/modelQuality/mockRuntimeEligible truth와 exact-31 pointer 후보 검증
3. Signal, model evaluation, backtest, ingest status의 같은 run/source hash 확인
4. Team A production image build와 실제 UI 흐름 검증
5. exact-33, full Compose 5/7, ordinary security, clean clone 재검증
6. Team PR을 merge commit으로 병합하고 exact merge SHA post-merge CI와 main ancestry 확인

Owner adapter, backend, OpenAPI, migration 또는 계약을 고쳐 Team 실패를 우회하지 않습니다. 실패는
`TEAM_A_CONTRACT_VIOLATION` 또는 `TEAM_B_CONTRACT_VIOLATION`과 수정할 Team 파일만 돌려줍니다.

## 4. KIS Mock certification과 activation

Team 통합은 physical call 0으로 끝냅니다. 별도 exact 사용자 승인과 credential이 있을 때만 Owner가 다음을
실행합니다.

```bash
./capstone mock configure
./capstone mock doctor
./capstone mock certify --symbol 005930 --quantity 1
```

certification은 지정가 BUY 1주, 즉시 전량취소, 최종 체결 0, 미체결 0, pre/post balance 동일을 요구하며
retry·자동매도·재주문은 0입니다. 현재 recurring automation은 비활성이고 exact `mock start/stop`, 실제
scheduler wiring과 3-session soak 증거는 아직 없습니다. 이를 Team A/B 작업으로 넘기지 않고, Team 결과와
certification PASS 뒤 별도 Owner activation 승인 범위에서만 구현·검증합니다.

## 5. fresh clone과 공급망

각 Team merge SHA마다 별도 임시 directory에서 다음을 재현합니다.

```bash
./capstone doctor
./capstone up
./capstone smoke
./capstone status
./capstone team-a acceptance
```

- [ ] 기본 persistent 5, models 7 healthy
- [ ] one-shot exited residual 0, Docker socket 0, loopback bind
- [ ] restart recovery와 named volume 보존
- [ ] provider/account/order call 0
- [ ] Team B restricted immutable OCI digest, receipt, SBOM, provenance, signature PASS

## 6. Release 판정

Team 결과 병합만으로 `FINAL`이 되지 않습니다. Vertex live, KIS Mock certification/activation, 24-hour health,
연속 3개 pinned XKRX session soak와 v3 16개 hard gate가 전부 PASS하고 사용자가 Release를 별도로 승인해야
`1.0.0` tag와 GitHub Release를 만들 수 있습니다.

```text
GDELT_OUTBOUND_CALLS=0
KIS_LIVE_CALLS=0
CODEX_SECURITY_DEEP_SCAN=NOT_RUN_USER_SCOPED_OUT
RECURRING_AUTOMATION=DISABLED
P1_1_0_0_RELEASED=FALSE
```
