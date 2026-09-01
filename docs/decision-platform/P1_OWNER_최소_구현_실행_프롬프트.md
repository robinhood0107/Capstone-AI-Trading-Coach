# P1 Owner 최소 구현 실행 프롬프트

아래 프롬프트를 새 Codex 세션에 그대로 전달한다.

---

## 실행 프롬프트

`/home/pjjpj/projects/Capstone-AI-Trading-Coach`에서 P1 exact-31 LSTM 최소 통합을 완성해라.

### 최종 목표

P1 거래 경로의 confidence를 먼저 제거한 뒤 Owner 최소 통합을 완성한다. Team B가
`deploy/p1/seed/team-b/`에 exact-10 Git Model Seed를 commit하면 새 backend 설계 없이
`./capstone up`이 자동 검증·import·모델 load를 완료하고, 기존 automation runtime이 당일
Rule+LSTM 신호를 생성해 KIS Mock 게이트 앞까지 연결해야 한다.

Owner 구현을 모두 끝낸 후에는 Team B 요청서를 다시 읽고, Owner가 이미 완성한
항목과 선행 조건을 제거해 **Team B가 실제로 할 일만** 남긴 최종본을 만든다.

### 작업 원칙

1. 시작 전 `git status --short --branch`, HEAD, upstream, 기존 diff를 확인한다.
2. 사용자의 현재 문서 변경을 보존하고 reset·clean·stash·rebase·새 worktree를 사용하지 않는다.
3. 기존 `assets.py`, `importer.py`, `inference.py`, `automation_runtime.py`, V75/V88/V90/V93/V110,
   `deploy/p1/compose.yml`, `full-appctl`을 재사용한다.
4. 새 Return Engine 서버·새 persistent container·새 gRPC·새 scheduler·mTLS·Kubernetes·OCI/GHCR를
   추가하지 않는다.
5. confidence 제거를 가장 먼저 완료하고 그 계약을 이후 구현의 입력으로 사용한다.
6. KIS Live 주문·GDELT outbound는 0이다. KIS 읽기전용 학습 데이터 수집도 새 exact 승인
   전에는 물리 호출 0을 유지한다.
7. `CODEX_SECURITY_DEEP_SCAN=NOT_RUN_USER_SCOPED_OUT`을 유지한다.
8. 새 commit·PR 메시지에 `Co-Authored-By`, AI 도구명, 자동생성 기여 표식을 남기지 않는다.

### 구현 0 — confidence 먼저 제거

- 이 단계를 끝내기 전에 학습 input·Git Seed·daily inference·automation 구현을 시작하지 않는다.
- 범위는 P1 Return/LSTM/AI 거래 경로의 confidence로 한정한다. 달력·데이터 출처·RAG 등
  다른 domain의 confidence 개념은 수정하지 않는다.
- Return signal·artifact·daily batch·DB projection·Automation candidate·LLM request/response·Team A 표시에서
  confidence를 제거한다.
- 후보 기본 정렬은 `expectedReturn DESC, symbol ASC`로 고정한다.
- Strong LLM의 기존 judgement score·순위 변경·VETO·ABSTAIN·근거 동작과 기존 테스트는
  그대로 유지한다. confidence 제거를 이유로 AI 판단 방식·provider chain·VETO 정책·후보 집합을
  임의로 변경하지 않는다.
- confidence가 기존 수량에 주던 영향만 제거하고, 후보 추가·Risk 우회·수량 증가 권한은 계속 0으로 두며
  최종 주문수량은 기존 RiskEngine만 결정한다.
- 이미 봉인된 historical schema·contract-change·evidence bytes는 수정하지 않고, 현재 실행 계약만
  최소 versioned additive 변경으로 전환한다.
- 생성기·schema·example·validator·SQL·Python·Kotlin·generated client·UI를 같은 변경으로 동기화한다.
- Strong LLM 순위변경·VETO 전용 테스트를 새로 추가하거나 기존 테스트를 임의로 변경하지 않는다.
- confidence 변경으로 직접 깨지는 관련 contract test·Python/Kotlin focused test·Team A generated client
  check만 한 번 실행한다.
- 다음 표식을 모두 만족해야 구현 0을 완료한다.

```text
P1_RETURN_CURRENT_CONFIDENCE_FIELDS=0
P1_AUTOMATION_CURRENT_CONFIDENCE_FIELDS=0
P1_STRONG_LLM_CURRENT_CONFIDENCE_FIELDS=0
P1_TEAM_A_CURRENT_CONFIDENCE_FIELDS=0
STRONG_LLM_JUDGEMENT_AUTHORITY=RANK_VETO_ABSTAIN_ONLY
RISKENGINE_QUANTITY_AUTHORITY=1
HISTORICAL_CONFIDENCE_BYTES_CHANGED=0
```

### 구현 1 — Owner 학습 입력

- 이미 봉인된 exact-31 universe를 1.0.0까지 고정한다.
- 기존 bounded KIS market-data bootstrap을 수정해 RAW_CLOSE 일봉 약 756 XKRX session을 수집한다.
- provider credential·account·balance·order 데이터를 input pack에 넣지 않는다.
- transient 실패 operation만 1회 재시도하고, 성공 operation을 재호출하지 않는다.
- raw provider response를 저장하지 않고 normalized Parquet·manifest·receipt만 봉인한다.
- 기존 `p1-return-engine-input-pack.v1`을 재사용해 ZIP 하나와 manifest SHA-256을 만든다.
- 물리 KIS 실행 직전에 provider·operation·호출 상한·retry·비용을 포함한 승인을 받는다.

### 구현 2 — Git Model Seed 자동 적재

- `deploy/p1/seed/team-b/` exact-10과 manifest를 기존 production importer로 먼저 검증한다.
- `./capstone up`의 migration 뒤 Team B Seed import one-shot을 추가한다.
- 첫 import는 `IMPORTED`, 동일 manifest 재실행은 `REPLAYED`/no-op이어야 한다.
- hash·schema·exact-10 실패 시 Decision Platform 활성화를 중단한다.
- Git Seed를 Decision Platform에 read-only mount하고 기존 Return inference process가 해당
  `model.safetensors`·`scaler.json`을 로드하게 한다.
- P1은 모델 한 세대만 사용하며 자동 재학습·자동 교체를 만들지 않는다.

### 구현 3 — daily inference 최소 연결

- 새 scheduler를 만들지 말고 기존 `AutomationRuntimeService.serve()`의 XKRX session claim 직전에
  `ensure_daily_signals(sessionDate)` 한 단계만 추가한다.
- 당일 daily signal이 있으면 no-op하고, 없으면 직전 완료 XKRX session의 검증된
  market-data로 exact-31 입력을 만든다.
- 기존 loopback `ReturnInferenceService` 한 번과 기존 MA5·MA20·RSI rule을 사용한다.
- base model artifact와 daily signal batch를 분리하는 additive V116 migration을 추가한다.
- daily batch는 exact-31 LSTM·rule, model hash, source/target session, market manifest hash에 결속한다.
- 같은 session/hash는 no-op, 같은 identity/different bytes는 전체 rollback한다.
- inference는 동일 요청으로 1회만 재시도한다. 두 번 실패하면 신규 BUY만 0으로 두고
  기존 포지션 청산·대사·UI는 계속 동작한다.
- stale·전날 신호·synthetic·legacy preview를 production daily pointer로 사용하지 않는다.

### 구현 4 — 정책 drift 수정과 문서

- Python importer, V88 후속 migration, active model pointer, inference loader의
  `PASS | BELOW_BASELINE + mockRuntimeEligible=true` 정책을 일치시킨다.
- `P1_OWNER_PHASE_A`, `OWNER_POST_TEAM_CODE_REQUIRED`, Team A/B 후속 실행표, 운영 가이드를 현재
  사실로 동기화한다. 과거 truth-freeze 문서는 수정하지 않는다.
- 전체 아키텍처 문서에 `Git Model Seed → existing inference → existing automation loop`을 간단히 기록한다.
- Kubernetes는 후속 TODO 한 줄만 남기고 manifest·CronJob·Helm을 만들지 않는다.

### 구현 5 — Owner 완료 후 Team B 요청서 최종 축소

Owner 구현과 focused 검증이 완료된 뒤에만 다음 문서를 수정한다.

```text
docs/handoff/P1_TEAM_B_최종_통합_요청서.md
docs/decision-platform/P1_TEAM_B_RETURN_ENGINE_완료_요청서.md
```

- `DRAFT_OWNER_PREREQUISITE`를 제거한다.
- Owner가 이미 완성한 confidence·KIS 수집·input 생성·Seed import·daily inference·DB·Compose·
  automation 설명과 선행 요청을 삭제한다.
- Team B에게 실제로 전달할 ZIP 파일명과 manifest SHA-256를 입력란에 고정한다.
- confidence 작업으로 확정된 현재 artifact schema·manifest version·파일 컬럼만 명시한다.
- 남길 항목은 exact-31 production 학습 1회, rule, backtest, exact-10 Git Model Seed, focused test,
  PR·hash·성능 보고뿐이다.
- 새로 완성된 코드를 Team B에게 다시 구현하라고 요구하지 않는다.
- repository·Desktop 요청서 bytes를 동기화하고 SHA-256 일치를 확인한다.
- 관련 documentation contract test만 한 번 실행한다.

### 검증 정책 — 반복 금지

구현 중에는 변경 표면의 focused test만 실행한다. Docker image build, full Gradle,
full Python, full contracts, Compose E2E, GitHub CI는 중간에 실행하지 않는다.

```text
contracts 변경       -> 해당 generator --check + 해당 contract test
importer/inference 변경 -> test_importer.py + test_inference.py
automation 변경      -> test_automation_runtime.py의 관련 케이스
V116 변경           -> 전용 migration contract/integration test
Compose 변경        -> docker compose config --quiet
docs 변경           -> 관련 documentation test만
```

- 이미 PASS한 동일 범위를 코드 변경 없이 다시 실행하지 않는다.
- 테스트 실패 후에는 실패한 영역과 직접 관련된 명령만 재실행한다.
- confidence부터 Owner 코드·Team B 요청서 최종화까지 모두 끝난 뒤 Docker build·full Python·
  Kotlin·contracts·Compose E2E·일반 보안·공급망 검증을 각각 한 번만 실행한다.
- Team B Model Seed가 필요한 최종 E2E는 Seed 수신 전에 반복하지 말고 fixture-focused 검증만
  완료한다.
- 장중 KIS Mock·3 XKRX session soak는 dummy/provider-free E2E로 대체하지 않고 마지막 운영
  게이트로 만 남긴다.

### 커밋·PR·병합

- 과거 증거·contract-change·truth-freeze bytes를 수정하지 않는다.
- 새 계약·Owner runtime·문서 동기화를 필요 최소 commit으로 묶는다.
- 모든 focused 검증이 통과하면 한 번 push하고 하나의 PR을 만든다.
- commit 직전 `git diff --cached --check`와 commit message 원문을 확인해 contributor·co-author trailer가 0인지
  검증한다.
- required CI를 한 번 확인하고 실패한 job만 원인 수정 후 재실행한다.
- 필수 CI·review가 통과하면 병합하고 main post-merge 필수 검사만 한 번 확인한다.
- 사용자 승인 없이 tag·Release·provider·account·order 호출을 실행하지 않는다.

### 완료 보고

다음을 서로 다른 상태로 보고한다.

```text
IMPLEMENTED=<PASS|PARTIAL>
FOCUSED_TESTED=<PASS|NOT_RUN>
FULL_TESTED=<PASS|NOT_RUN>
MERGED=<YES|NO>
CURRENTLY_RUNNABLE=<YES|BLOCKED>
TEAM_B_REAL_ARTIFACT_V2=<PASS|BLOCKED>
TEAM_B_GIT_MODEL_SEED=<PASS|BLOCKED>
DAILY_INFERENCE=<PASS|BLOCKED>
KIS_MOCK_MARKET_SESSION=<PASS|NOT_RUN|BLOCKED>
THREE_XKRX_SESSION_SOAK=<PASS|NOT_RUN>
CODEX_SECURITY_DEEP_SCAN=NOT_RUN_USER_SCOPED_OUT
KIS_LIVE_ORDER_CALLS=0
GDELT_OUTBOUND_CALLS=0
P1_FINAL=<PASS|NOT_READY>
```

Team B Model Seed가 아직 없는 동안에도 synthetic·legacy preview를 real로 꾸미지 말고 Owner 코드와
focused 검증을 끝까지 진행한다.
