# Team A·B 완료 후 Owner 최종 실행표

## 이 문서의 용도

Team A UI PR과 Team B `REAL_TEAM_B` bundle을 모두 받은 뒤 Owner가 통합·검증·모의운용·
릴리스 판정을 진행하는 현재 실행표다.

`P1_TEAM_A_B_수신_후_통합_체크리스트.md`는 `HISTORICAL_SUPERSEDED`로 동결된 과거 기록이다.
두 문서가 다르면 이 문서를 따른다.

이 문서 자체는 provider·계좌·주문 실행 승인이 아니다. 물리 호출은 provider, operation,
계정 mode, 최대 호출·retry·비용이 명시된 새 승인과 credential이 있을 때만 진행한다.
KIS Live 주문과 GDELT outbound는 항상 0이다.

## 0. 완료 정의

다음을 서로 다른 상태로 기록한다.

```text
IMPLEMENTED=<PASS|PARTIAL>
FOCUSED_TESTED=<PASS|NOT_RUN>
FULL_TESTED=<PASS|NOT_RUN>
MERGED=<YES|NO>
CURRENTLY_RUNNABLE=<YES|BLOCKED>
PHYSICAL_ACTIVATION=<PASS|NOT_RUN|BLOCKED>
P1_FINAL=<PASS|NOT_READY>
```

코드 구현, 로컬 테스트, PR 병합, 현재 Compose 기동, 장중 모의주문, 릴리스를
서로 대체 증거로 사용하지 않는다.

## 1. 수신본 고정

### Team A

- [ ] PR URL과 commit SHA
- [ ] `package-lock.json` SHA-256
- [ ] `p1-team-a-acceptance.v4` exact-45, Playwright skip 0
- [ ] production fake response 0, same-origin `/api`
- [ ] desktop·mobile 대표 화면과 남은 blocker

### Team B

- [ ] PR URL과 commit SHA, `uv.lock`·Dockerfile SHA-256
- [ ] owner input manifest SHA-256과 production manifest SHA-256
- [ ] exact-10 파일명·크기·SHA-256 표
- [ ] 고정 설정 production 학습 1회 결과
- [ ] daily inference 실행 명령과 동일 bytes 검증
- [ ] `evidenceMode=REAL_TEAM_B`, `realTeamB=true`, `orderAuthority=NONE`
- [ ] `modelQuality=PASS | BELOW_BASELINE`과 `mockRuntimeEligible` 근거

성능이 baseline보다 낮은 것은 수신 거부 사유가 아니다. `BELOW_BASELINE`을 그대로
공개하고 input binding, leakage 0, train-only scaler, 35bps 비용, 재현성과 독립 metric
재계산으로 적격성을 판정한다. 성능 개선을 위한 추가 학습은 하지 않는다.

## 2. 병합 전 독립 검증

- [ ] 현재 branch, HEAD, upstream, worktree 상태 기록
- [ ] 사용자의 관련 없는 변경을 reset·clean·stash로 제거하지 않음
- [ ] Team A·B PR을 각각 검토하고 required CI 확인
- [ ] secret, symlink, 개인 절대경로, raw provider 응답, 큰 output이 Git에 없음
- [ ] Team B legacy CSV·PTH와 production `model.safetensors`를 구분

Team B bundle은 import 전에 반드시 read-only validator를 먼저 실행한다.

```bash
./capstone artifact validate <team-b-bundle> --manifest-sha256 <sha256>
```

- [ ] exact manifest v3와 confidence 없는 exact-10 inventory 통과
- [ ] safetensors 31 namespace × 14 tensor, F32·shape·finite 통과
- [ ] config·scaler·golden·input·producer hash binding 통과
- [ ] trade·equity·backtest 독립 재계산 통과
- [ ] validator 중 provider·account·order 호출 0

## 3. 병합과 clean checkout 재현

1. Team A·B PR의 필수 CI와 review를 확인한다.
2. 각 PR을 병합하고 main post-merge CI를 확인한다.
3. 별도 clean checkout에서 main merge SHA를 재현한다.

```bash
./capstone doctor
./capstone up
./capstone status
./capstone smoke
./capstone team-a acceptance
```

- [ ] Team A acceptance exact-45와 Playwright skip 0
- [ ] Dashboard production fake response 0
- [ ] 기본 5개 persistent container healthy
- [ ] one-shot residual 0, Docker socket mount 0
- [ ] `./capstone down` 후 named volume 보존

## 4. Team B 적재와 projection

validator가 통과한 동일 bundle과 manifest SHA만 import한다.

```bash
./capstone up
./capstone artifact import <team-b-bundle> --manifest-sha256 <sha256>
```

- [ ] 첫 import는 `IMPORTED`, 동일 packet 재실행은 `REPLAYED`·archive no-op
- [ ] content-addressed archive와 integrity receipt 일치
- [ ] active `REAL_TEAM_B` pointer와 source/release binding 확인
- [ ] Signal, Model Evaluation, Backtest, Ingest Status에 같은 `runId`·source hash 표시
- [ ] daily shard inference의 `sessionDate`가 현재 XKRX session과 다르면 신호 0
- [ ] Team B 신호에 주문 권한 0

## 5. provider-free 관통 검증

- [ ] 로그인 → 원칙 → Risk → 주문 검토 → 자동운용 → RAG·학습일지 UI 흐름
- [ ] Rule+LSTM 후보 → Decision/Risk/Kill Switch → brokerage adapter 전까지
- [ ] HOLD·BLOCK·ABSTAIN·stale·HALTED에서 주문 0
- [ ] 재시작·중복 tick·부분체결·대사 안전성
- [ ] 로컬 기본 mode의 provider·account·order 물리 호출 0

장외에서는 다음 replay를 중간 증거로 사용할 수 있다.

```bash
P1_PROJECT_NAME=capstone-p1-after-hours-replay \
P1_STATE_DIR=deploy/p1/.state-after-hours-replay \
./capstone test after-hours-replay --manifest-sha256 <accepted-manifest-sha256>
```

after-hours replay는 장중 KIS Mock 인증과 실제 `THREE_XKRX_SESSION_SOAK`를 대체하지 않는다.

## 6. 외부·장중 hard gate

아래 단계는 각각 신규 승인과 credential이 필요하다. 승인 전에는 실행하지 않는다.

1. actual KIS read-only market-data bootstrap과 daily shard
2. 실제 Vertex Google grounding의 bounded BUY veto
3. XKRX 장중 KIS Mock certification
4. 모든 readiness gate PASS 후 arm/start
5. 연속 3 XKRX session soak와 대사

KIS Mock의 승인된 실행 순서는 다음 CLI 표면을 사용한다.

```bash
./capstone up
./capstone mock configure
./capstone mock doctor
./capstone mock certify --symbol 005930 --quantity 1
./capstone up --mock
./capstone mock gate-author
./capstone mock readiness
./capstone mock start
./capstone mock stop
```

- [ ] KIS Mock만 사용하고 KIS Live 주문 0
- [ ] 인증·readiness·start의 blocker를 숨기지 않음
- [ ] 일일 신규 BUY 주문 상한, 09:20 cutoff, 15:20 cancel·reconcile 유지
- [ ] retry·reorder·묵시적 INTERNAL_PAPER fallback 0
- [ ] 부분체결·cancel 실패·account drift는 `HALTED` 또는 `PENDING_RECONCILIATION`
- [ ] 세 세션 각각의 content-free receipt·order/fill/reconciliation·restart 증거 보존

## 7. 최종 16개 release gate

아래가 모두 `PASS`일 때만 `P1_FINAL=PASS`, `1.0.0` tag와 GitHub Release를 승인한다.

- [ ] `P1_CORE`
- [ ] `PUBLIC_RAG_SEED`
- [ ] `OWNER_RAG_BACKEND`
- [ ] `BGE_OCR_CPU_INTEL`
- [ ] `MARKET_DATA_DAILY`
- [ ] `TEAM_B_REAL_ARTIFACT_V2`
- [ ] `TEAM_A_REAL_UI_33` — release gate의 보존된 이름이며 현재 acceptance는 v3 exact-45
- [ ] `VERTEX_NEWS_VETO`
- [ ] `JOURNAL`
- [ ] `AUTOMATION_CLOSED_LOOP`
- [ ] `LIGHTGBM_RESEARCH_DISCLOSURE`
- [ ] `SECURITY_RELEASE`
- [ ] `SUPPLY_CHAIN_RELEASE`
- [ ] `OCI_REPRODUCIBILITY`
- [ ] `COMPOSE_E2E`
- [ ] `THREE_XKRX_SESSION_SOAK`

`SECURITY_RELEASE`는 일반 보안·의존성·secret·container 검증으로 판정한다.

```text
CODEX_SECURITY_DEEP_SCAN=NOT_RUN_USER_SCOPED_OUT
KIS_LIVE_ORDER_CALLS=0
GDELT_OUTBOUND_CALLS=0
```

## 8. 최종 보고 템플릿

```text
TEAM_A_IMPLEMENTED=<PASS|PARTIAL>
TEAM_A_ACCEPTANCE_EXACT45=<PASS|NOT_RUN>
TEAM_A_MERGED=<YES|NO>
TEAM_B_CODE_IMPLEMENTED=<PASS|PARTIAL>
TEAM_B_REAL_ARTIFACT_V2=<PASS|BLOCKED>
TEAM_B_MODEL_QUALITY=<PASS|BELOW_BASELINE|UNKNOWN>
TEAM_B_MERGED=<YES|NO>
COMPOSE_E2E=<PASS|NOT_RUN|BLOCKED>
AFTER_HOURS_REPLAY=<PASS|NOT_RUN|BLOCKED>
MARKET_DATA_DAILY=<PASS|NOT_RUN|BLOCKED>
VERTEX_GROUNDING_LIVE=<PASS|NOT_RUN|BLOCKED_APPROVAL>
KIS_MOCK_MARKET_SESSION=<PASS|NOT_RUN|BLOCKED>
THREE_REAL_XKRX_SESSION_SOAK=<PASS|NOT_RUN>
CURRENTLY_RUNNABLE=<YES|BLOCKED>
CODEX_SECURITY_DEEP_SCAN=NOT_RUN_USER_SCOPED_OUT
KIS_LIVE_ORDER_CALLS=0
GDELT_OUTBOUND_CALLS=0
P1_FINAL=<PASS|NOT_READY>
P1_1_0_0_RELEASED=<TRUE|FALSE>
```

이 템플릿에서 `BLOCKED`나 `NOT_RUN`을 임의로 `PASS`로 바꾸지 않는다.
