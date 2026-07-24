# 선물옵션 모의주문 확장 시나리오 API 명세서

작성일: 2026-06-23
모델 위험·근거 검토일: 2026-07-17
상위 문서: `최종_프로젝트_명세서.md`, `API_명세서.md`, `선물옵션_모의주문_확장_시나리오.md`
범위: P1 API를 전제로 추가되는 P2 국내선물옵션 주간 KIS Mock API

> 구현 상태: 이 문서는 v1/P1 필수 구현 계약이 아니라 후속 P2 확장 API 초안이다. 팀원 1의 현재 구현 시작 범위에서는 국내주식/금 ETF·ETN 기반 Principle, Decision, Risk, RAG, KIS Mock API를 우선한다.
>
> 용어 경계: 이 문서의 `limitPrice`는 P2 `derivativeOrderIntent` 전용이다. 현물 v1 Decision
> `orderIntent`는 MARKET/LIMIT 모두 `estimatedPrice`만 사용하며 두 schema/hash를 섞지 않는다.
>
> 문서의 `201V6300`과 관련 ID/수치는 수학·schema 검증용
> `SYNTHETIC_NOT_ORDERABLE` project fixture다. 실제 KIS 9자리 option `SHTN_PDNO`나
> option-right mapping이라고 주장하지 않는다. exact official master fixture 승인 전
> confirm/submit 예시는 accepted response가 아닌 wire-shape 설명이고 provider 호출은 0건이다.

---

## 1. 문서 목적

이 문서는 P1 API를 다시 정의하지 않는다. P1의 Principle, Decision, Risk, RAG, Signal, Backtest, Brokerage Mock, Journal, Financial Engineering API를 전제로 두고, 그 상위에 추가되는 P2 국내선물옵션 Mock 주문 API만 정의한다.

KIS endpoint와 TR ID 매핑, 모의 지원/미지원 경계는 이 문서에서만 관리한다. P1 공식 문서에는 국내선물옵션 주문 API를 포함하지 않는다.

---

## 2. P1 상속/전제 API

| P1 API | P2에서 상속하는 역할 | P2 확장 방식 |
|---|---|---|
| Principle API | 사용자 투자 원칙 CRUD, rule versioning, enabled/disabled 관리 | 파생상품 후보도 동일한 원칙 version을 참조 |
| Decision API | 주문 후보 평가, ALLOW/WARN/HOLD/BLOCK 판정 | `derivative_order_intent`를 입력받는 확장 경로 추가 |
| Risk API | 손실한도, 포지션한도, stale data, kill-switch | 상품군 allowlist, 증거금, DTE, Greeks, IV, 유동성 guard 추가 |
| RAG API | 출처 기반 금융 설명, citationCoverage, retrievalFailure | 옵션 매도 위험, 증거금, TR 응답 근거 설명에 사용 |
| Signal API | rule baseline, LSTM, LightGBM, HMM 국면 조회 | 방향성 후보 생성 시 보조 신호로만 사용 |
| Backtest API | Baseline/Guide/Strict 성과 비교 | P2 주문은 backtest 성과가 아니라 Mock risk scenario 재현으로 검증 |
| Brokerage Mock API | 주식/ETF·ETN Mock 주문, 취소, 잔고, 체결 event | 별도 `DerivativeMockGateway`를 추가하되 idempotency/audit 규칙은 상속 |
| Journal API | decision/backtest/RAG 근거 기록 | margin/exposure/stress snapshot, optional valuation/regime evidence ref, KIS TR ID, fill/position reconciliation 기록 추가 |
| Financial Engineering API | BSM, Greeks, IV, VaR/CVaR, HMM, Monte Carlo 계산 | BSM/Greeks/IV/HMM/Monte Carlo는 설명·WARN evidence다. fresh 가격·owner position·margin·손실한도·probability-free stress 같은 deterministic guard와 분리 |
| Async Status API | async job 상태, stream metric, artifact ingest 상태 | P2 내부 async event 상태는 Spring 상태 조회 API로만 노출 |

---

## 3. 공통 원칙

1. 프론트엔드는 Spring REST API만 호출한다.
2. Python KIS Adapter는 내부 gRPC 실행 어댑터다.
3. 최종 주문 승인권은 Spring Decision Platform과 RiskEngine에 있다.
4. P2 주문은 `KIS_DERIVATIVE_MOCK`에서만 가능하다.
5. P2 주문 session은 client enum이 아니라 validated KRX calendar·상품별 정규/최종거래일
   시간과 server clock에서 파생한 `DAY`만 허용한다. 휴장·야간·`now>=lastTradingAt`은
   provider 호출 전에 차단한다.
6. Live-ready 경로는 기본 OFF다.
7. 주문 후보 생성은 주문 실행이 아니다.
8. 주문 전 `marginSnapshot`과 `riskDecision`이 없으면 fail-closed 한다.
9. 주문 제출·정정취소·Live safety gate처럼 금융 부작용을 만드는 쓰기 API는 P1의 `(subject, method, route-template, key)` idempotency 계약을 요구한다. 단순 조회·후보 생성에는 이를 자동 적용하지 않는다.
10. 모든 주문 이벤트는 `decisionId`, `marginSnapshotId`, `exposureSnapshotId`,
    `stressSnapshotId`, `orderIntentId`, `logicalActionId`, `actionGeneration`,
    `submissionAttemptId`, `riskReservationId`, `attemptGeneration`, `physicalAttemptCount`,
    `kisTrId`, `requestId`,
    `idempotencyKeyHash`, sanitized provider receipt/hash를 audit log에 남긴다. 생성된 경우의
    `valuationSnapshotId`·typed model evidence reference는 optional로 남긴다. provider raw
    응답, provider 계좌번호, token/key, idempotency key 원문은 남기지 않는다.
11. P2 내부 async event는 기본 OFF이며, 실제 세부 형식은 공개 API 계약 밖의 Decision Platform 내부 구현 기록에서 관리한다.
12. P1의 Decision 유효시간 규칙(`validUntil`, 만료 시 `DECISION_EXPIRED`)을 파생 decision에도 그대로 상속한다. margin snapshot은 자체 `expiresAt`을 추가로 가진다. provider claim 전에 하나라도 만료되면 제출을 거부하고 intent를 EXPIRED로 전이한다. `SUBMISSION_CLAIMED` 이후에는 과거 evidence 만료를 새 expiry/renew 권한으로 사용하지 않는다.
13. 모든 `accountId`는 provider 계좌번호가 아닌 opaque 내부 ID이며 JWT subject 소유권을 조회 조건에 포함한다. 다른 사용자의 account/order/decision/snapshot/receipt ID는 존재 여부를 숨기고 `NOT_FOUND`로 거부하며 provider 호출을 만들지 않는다.
14. post-trade naked short call 노출 생성·증가의 2단계 확인은 단순 `side=SELL`이 아니라 server-reconstructed post-trade
    naked short call 노출이 생성·증가할 때 적용하며 boolean이 아닌 서버 발급 receipt 두
    개로 증명한다. long option sell-to-close는 일반 submission, 지원 중인 naked short
    call의 buy-to-close는 위험감소 경로다. unsupported position은 account-level HOLD다. 각
    receipt는 subject, opaque accountId, orderIntentId, logicalActionId, action generation,
    decisionId, marginSnapshotId, exposureSnapshotId, stressSnapshotId,
    `canonicalActionHash`, 단계별로 서로 다른 `confirmationViewId`와
    `confirmationDisclosureHash`, 단계, issuedAt, 짧은 TTL에 묶는다. 일반 주문의
    submission receipt는 submit claim에서 한 번 소비한다. 이 naked-short branch의 step1 receipt는
    step2 발급에서 한 번 소비하고 step2 receipt는 submit claim에서 한 번 소비한다.
15. provider credential과 허용 계좌는 배포 운영자만 관리한다. Live gate는 immutable OFF가 기본이며 공개 API로 credential, account allowlist, 배포 gate를 변경할 수 없다.
16. P2 KIS Adapter는 최종 명세서 12.4.1의 P1 중앙 account/appkey+mode quota coordinator를 재사용한다. 모의 REST 1/s는 시세·호가·주문가능·주문·정정취소·체결/잔고 대사와 모든 물리 재시도의 합산이며 주문/대사가 backfill보다 우선한다.
17. P2 WebSocket은 P1과 같은 appkey별 physical session 하나와 합산 subscription ledger를 공유한다. 별도 session이나 별도 41개 budget을 만들지 않는다.
18. 수치가 재현된다는 사실은 모델의 통계적·경제적 유효성을 증명하지 않는다. 모든 model evidence는 계산 정확성, 통계적 유효성, 경제/운영 유효성을 별도로 기록한다.
19. BSM/Greeks/IV는 위험중립 `Q` 아래 European 이론가·민감도다. 실제 방향확률, 기대수익률 또는 단독 주문 신호로 사용하지 않는다.
20. HMM은 `x_1..x_t`만 사용하는 2-state causal forward posterior다. `maxPosterior>=0.65`가 v1 운영 gate이고 normalized entropy는 secondary diagnostic이다. `hmm-regime-policy-v1`에서 entropy가 exact `>0.95`일 때만 `HIGH_POSTERIOR_ENTROPY`; equality는 warning이 아니다. latent label은 실제 시장 원인이 아니며 gate를 통과해도 WARN-only다.
21. LightGBM raw class score는 public `confidence`가 아니다. 시간순 OOS calibration을 통과한 값만 confidence로 사용하고 실패·drift 시 모델 evidence를 `ABSTAIN`한다.
22. 기대 payoff는 `measure`, discounting, horizon, estimator/model과 함께 기록한다. 실제분포
    `P`의 예측 기대값과 위험중립 `Q`의 무차익 가격은 서로 대체하지 않으며 어느 쪽도 단일
    경로의 보장값이 아니다.
23. stochastic GBM scenario와 deterministic stress를 분리한다. GBM의 IID Gaussian,
    lognormal, constant-vol, no-jump 가정과 Monte Carlo error를 기록하고 stress의
    `probability`는 required `null`이다. historical/block bootstrap과 jump/fat-tail
    estimator는 `RESEARCH_ONLY`다.
24. 금융 Brownian/GBM diffusion과 DDPM의 학습된 reverse denoising process는 같은
    알고리즘이 아니다. RAG 개념 비교 외에 모델·API를 교환하지 않는다.
25. contract notional, premium cashflow, conservative delta-equivalent exposure, market value, margin, stress loss, max loss는 서로 다른 값이다. 어느 하나를 다른 값의 이름으로 반환하지 않는다.
26. 옵션 매수와 매도의 손실 구조를 구분한다. naked short call은 `UNBOUNDED`이며 유한한 `maxLossAmount`를 만들지 않는다.
27. 모델 내부 `ABSTAIN`과 public wire 생성 실패를 구분한다. 현재 P0 Signal/RiskDecision success schema의 필수 HMM component를 만들 수 없으면 기존 service/schema failure 경로로 `HOLD`하며, 임의 state를 채우지 않는다. 이 HOLD는 HMM의 경제적 주문 권한이 아니라 필수 교환 계약의 가용성 경계다.
28. 이 문서의 component별 `AVAILABLE|ABSTAIN` model evidence는 P2 계획 계약이다. 실제 활성화 전에 `contracts/changes/`에서 Signal/RiskDecision schema, examples, API failure mapping을 함께 승인한다.
29. provider outbound 전 terminal submit receipt(일반 `submissionReceiptId` 또는 옵션
    매도 step2 receipt) 소비, option-short step1 consumed-lineage 검증, decision one-use, DB의
    `UNIQUE(subject, method, routeTemplate, idempotencyScopeHash)` 금융 부작용 claim,
    `SUBMIT_READY→SUBMISSION_CLAIMED` CAS를 한 DB transaction으로 선점한다.
    `canonicalRequestHash`는 unique key에 넣지 않고 저장·비교 열로 사용해 같은 key와 다른
    payload를 `IDEMPOTENCY_CONFLICT`로 거부한다. P1 Redis idempotency는 요청 replay
    조정용이며 cross-system 원자성을 가장하거나 금융 부작용 권한을 단독 소유하지 않는다.
30. claim transaction은 receipt, decision과 모든 hard snapshot의 최소 만료시각을
    `sendNotAfter`로 저장한다. quota 대기 뒤 private transport의 첫 byte 직전에 현재 시각,
    active action fence, attempt owner/generation을 다시 확인하고
    `physicalAttemptCount=0→1`을 DB CAS한 worker만 한 번 전송할 수 있다. deadline 초과나
    generation 불일치는 provider 호출 0건이며, deadline 초과는
    `SUBMISSION_FAILED/NOT_SENT`로 종결한다.
31. `(owner, account, canonicalActionHash)` active submission fence는
    `SUBMISSION_CLAIMED|PENDING_RECONCILIATION` 동안 같은 intent·다른 intent·다른
    idempotency key의 두 번째 outbound를 모두 막는다. provider first byte 전 EXPIRED된
    action의 renew만 같은 `logicalActionId`와 증가한 generation을 유지한다. provider
    attempt가 terminal로 해소된 뒤 사용자가 새 주문을 의도하고 새 확인을 완료한 경우에는
    새 `logicalActionId/actionGeneration=1`을 만든다.
32. timeout·불명확 응답은 자동 재시도하지 않고 `PENDING_RECONCILIATION`로 격리한다.
    empty/부분 page·조회 timeout·provider 반영 지연 전 조회는 미접수 증거가 아니다.
    provider가 접수 부재를 권위 있게 증명하지 못하면 operator review 상태로 계속
    fail-closed한다.
33. Kill Switch, product allowlist, owner/account 활성 상태와 운영자 account allowlist는
    active principle/policy version, required hard-data invalidation과 함께 같은 DB
    safety-gate row의 `safetyGateGeneration`을 증가시키며 직렬화한다. first-byte CAS는 그
    row를 잠그고 현재 gate 상태·generation, active principle exact version, required
    hard-data freshness와 validated KRX calendar·상품별 session에서 server-derived `DAY`,
    휴장/최종거래일 종료 전(`now < lastTradingAt`)을 다시 검증한다.
    gate 변경 transaction이 먼저 선형화되면 count 0 attempt는
    `SUBMISSION_FAILED/NOT_SENT`, provider 호출 0건이다. send-start CAS가 먼저 선형화돼
    count가 1이면 이미 시작된 attempt로 대사하되 자동 재전송하지 않는다.
34. active fence의 `canonicalActionHash`와 확인 화면의
    `confirmationDisclosureHash`를 분리한다. 전자는 provider-bound 불변 주문 필드만
    포함하며 quote·margin·exposure·stress·model evidence 변경으로 달라지지 않는다.
    후자는 사용자가 확인한 시간가변 loss/stress 설명을 결속하지만 중복 주문 fence key로
    사용하지 않는다.
35. 서로 다른 action도 같은 position·margin·exposure 한도를 동시에
    소비할 수 있으므로 claim transaction은 owner/account의 `ACCOUNT_GLOBAL`, 해당
    `PRODUCT_GROUP`, server-derived `STRATEGY` risk ledger를 이 순서로 잠그고
    `riskLedgerVersionVector`를 CAS한다. 최신 position+open/pending order를 포함한 각
    scope capacity를 하나의 `riskReservationId`로 원자 예약한다. reservation은
    PENDING·ACK/open order 동안 유지하고, reject/not-sent/확정
    미접수에는 release, fill에는 position capacity로 전환, partial fill/cancel에는
    filled/open/cancelled 수량대로 split한다. provider 주문가능 응답만으로 이 내부
    RiskEngine 원자성을 대신하지 않는다. 모든 position/open-order mutation은 같은 risk
    ledger transaction에서 outstanding reservation과 `riskReductionOnly`를 재검증한다.
    count 0 reservation이 더는 유효하지 않으면 generation을 올려 INVALIDATED 처리하고
    attempt를 `SUBMISSION_FAILED/NOT_SENT`로 종결한다. count 1은 release하지 않고
    reconciliation한다.
36. ambiguous attempt의 자동 positive reconciliation은 explicit provider correlation이
    있거나, first-byte 전에 결속한 완전한 provider order baseline 이후의 정확히 한 신규
    주문을 식별할 수 있을 때만 허용한다. correlation이 없으면 account가
    `EXCLUSIVE_APP_WRITER`이고 immutable action·acceptance time이 모두 일치해야 한다.
    baseline에 있던 이전 주문, 복수 후보, manual/external writer 가능성은 ACK 증거가
    아니며 PENDING+operator review를 유지한다.
37. P2 write 계좌는 운영자가 전용 Mock account로 검증한
    `providerWriterPolicy=EXCLUSIVE_APP_WRITER`만 허용한다. HTS/manual/다른 app이 같은
    account를 쓸 수 있는 `SHARED_EXTERNAL_WRITER`는 universe·잔고 조회만 허용하고 주문
    candidate/evaluate 단계에서 HOLD한다. 내부 reservation으로 외부 writer의 동시 주문을
    막을 수 있다고 가정하지 않는다.
38. 정정은 미체결 수량 감소만 RiskEngine 감소 경로로 허용한다. 수량 증가, 가격·side·상품·
    order type 변경은 새 candidate→평가→확인→제출 절차를 요구한다. 취소·감소 정정의
    응답이 불명확하면 기존 open-order reservation과 action fence를 유지하며, provider가
    취소/감소된 미체결 수량을 권위 있게 확인한 뒤에만 해당 capacity를 release한다.
39. P2 write activation은 order submit, cancel/modify, authoritative fills/positions
    reconciliation의 exact provider mapping·offline fixture·failure test가 한 번에 PASS해야
    한다. cancel/modify가 `CANDIDATE_UNVERIFIED`인 동안 신규 provider order도 0건이다.

---

## 4. P2 공통 타입

### 4.1 AssetType

| 값 | 의미 |
|---|---|
| `DOMESTIC_FUTURE` | 국내선물 |
| `DOMESTIC_OPTION` | 국내옵션 |

### 4.2 OptionRight

| 값 | 의미 |
|---|---|
| `CALL` | 콜 |
| `PUT` | 풋 |
| `NONE` | 선물처럼 권리 구분이 없는 상품 |

### 4.3 PositionEffect

| 값 | 의미 |
|---|---|
| `OPEN` | 신규 진입 |
| `CLOSE` | 기존 포지션 청산 |
| `REDUCE` | 위험 노출 축소 |
| `ROLL` | future-only enum. v1 `CANDIDATE_DISABLED`; multi-leg group/ordering/partial-fill/legging-risk/reservation 계약 승인 전 평가·제출 금지 |

### 4.4 DerivativeOrderType

| 값 | 의미 | 정책 |
|---|---|---|
| `LIMIT` | 지정가 | 기본 허용 |
| `MARKET` | 시장가 | 청산/위험감소 주문만 허용 |
| `CONDITION_LIMIT` | 조건부 지정가 | v1 `CANDIDATE_DISABLED`; exact provider code·trigger field·freshness 계약 승인 전 provider 호출 0건 |

### 4.5 ModelOutputKind과 Measure

| 타입 | 값 | 의미 |
|---|---|---|
| `ModelOutputKind` | `DESCRIPTIVE_STATISTIC` | 과거 관측 기반 기술통계 |
|  | `PREDICTIVE_PROBABILITY` | 실제분포 `P`에 대한 제한된 예측 추정 |
|  | `STOCHASTIC_SCENARIO` | 명시한 확률모형 아래 경로/분포 |
|  | `THEORETICAL_PRICE` | 가격결정 모형의 이론가·민감도 |
|  | `DETERMINISTIC_STRESS` | 확률을 붙이지 않은 조건부 충격 손실 |
| `Measure` | `P` | historical/physical distribution 추정 |
|  | `Q` | risk-neutral/no-arbitrage valuation |
|  | `NOT_APPLICABLE` | 측도 개념이 직접 적용되지 않음 |

### 4.6 LossProfile

| 값 | 의미 |
|---|---|
| `BOUNDED` | 현재 owner-scoped position과 모든 leg를 포함해 손실 상한을 유한값으로 계산 가능 |
| `UNBOUNDED` | naked short call처럼 이론상 유한한 손실 상한이 없음 |
| `POSITION_DEPENDENT` | reserved future value. covered/spread/assignment exact 계약 전 v1 직렬화 금지 |
| `LINEAR_MARGIN_DEPENDENT` | 선물처럼 premium 손실상한이 없고 선형 가격변화·variation margin·청산 조건으로 위험을 관리 |

`UNBOUNDED|LINEAR_MARGIN_DEPENDENT`이면 `maxLossAmount`는 반드시 `null`이다. 전자는
옵션 payoff의 무한 상방 위험, 후자는 선물의 선형·margin 의존 위험이므로 같은 의미로
합치지 않는다. margin requirement나 선택한 stress scenario의 손실을 최대손실로 대신하지
않는다.

### 4.7 ModelEvidenceStatus

| 값 | 의미 | 값 직렬화 |
|---|---|---|
| `AVAILABLE` | 지원 모델의 입력·수치·freshness 검증을 마친 WARN-only evidence | 이론가/Greeks/IV와 provenance를 포함 |
| `ABSTAIN` | 지원 모델이지만 stale, 입력 누락, 수치 실패 등으로 이번 evidence를 사용하지 않음 | common metadata·failure code·available input provenance는 필수, 이론가/Greeks/IV key는 모두 생략 |
| `UNSUPPORTED` | exercise style 등 계약 특성상 해당 estimator의 적용 대상이 아님 | model name·unsupported reason·contract reference만 필수, input/calculation/Greeks/IV key는 모두 생략 |

`status`는 evidence 가용성이고 `validationStatus=PASS|WARN|FAIL|NOT_APPLICABLE`은 검증 결과다.
`calibrationStatus=PASS|FAIL|NOT_APPLICABLE` 중 `PASS|FAIL`은 calibration 대상인
`PREDICTIVE_PROBABILITY`에만 사용한다. BSM/IV 같은 `THEORETICAL_PRICE`는 항상
`calibrationStatus=NOT_APPLICABLE`이다. IV 역산의 실행 결과는 별도
`ivInversionStatus=NOT_RUN|CALIBRATED_TO_INPUT_QUOTE|IV_NOT_BRACKETED|NUMERICAL_FAILURE`로
기록한다.

| `status` | 허용 `validationStatus` | BSM `calibrationStatus` | 허용 `ivInversionStatus` |
|---|---|---|---|
| `AVAILABLE` | `PASS`, `WARN` | `NOT_APPLICABLE` | `NOT_RUN`, `CALIBRATED_TO_INPUT_QUOTE` |
| `ABSTAIN` | `FAIL` | `NOT_APPLICABLE` | 네 값 모두 허용. stale이면 과거 역산 성공 상태를 보존하되 failure code로 사용 불가 사유를 명시 |
| `UNSUPPORTED` | `NOT_APPLICABLE` | `NOT_APPLICABLE` | `NOT_RUN` |

비-European 옵션의 BSM은 `status=UNSUPPORTED`, `validationStatus=NOT_APPLICABLE`이다.
European이지만 contract master 불완전·IV bracket 실패·non-finite이면
`status=ABSTAIN`, `validationStatus=FAIL`이다.
`ABSTAIN|UNSUPPORTED`에서 계산 key를 `null`로 채우거나 일부만 싣는 wire는 금지한다.
status별 required/forbidden key oneOf를 schema와 negative fixture로 고정하며, omitted과
explicit null을 같은 표현으로 취급하지 않는다.

---

## 5. Schema

### 5.1 derivative_order_intent

```json
{
  "schema": "derivative_order_intent",
  "lifecycleState": "EVALUATED",
  "orderIntentId": "doi_20260623_001",
  "logicalActionId": "dla_20260623_001",
  "actionGeneration": 1,
  "decisionId": "dec_deriv_20260623_001",
  "canonicalActionHash": "0000000000000000000000000000000000000000000000000000000000000000",
  "actionHashKeyVersion": "hmac-key-v1",
  "providerWriterPolicy": "EXCLUSIVE_APP_WRITER",
  "riskLedgerVersionVector": [
    {"scope": "ACCOUNT_GLOBAL", "scopeId": "mock-derivatives-01", "generation": 42},
    {"scope": "PRODUCT_GROUP", "scopeId": "KOSPI200_OPTION", "generation": 17},
    {"scope": "STRATEGY", "scopeId": "HEDGE", "generation": 9}
  ],
  "accountId": "mock-derivatives-01",
  "brokerageMode": "KIS_DERIVATIVE_MOCK",
  "kisTrId": "VTTO1101U",
  "assetType": "DOMESTIC_OPTION",
  "productGroup": "KOSPI200_OPTION",
  "symbol": "201V6300",
  "underlyingSymbol": "KOSPI200",
  "maturity": "2026-09",
  "lastTradingDate": "2026-09-10",
  "lastTradingAt": "2026-09-10T15:20:00+09:00",
  "finalSettlementDate": "2026-09-11",
  "exerciseStyle": "EUROPEAN",
  "settlementType": "CASH",
  "contractMultiplier": 250000,
  "strikePrice": 310.0,
  "optionRight": "PUT",
  "side": "BUY",
  "positionEffect": "OPEN",
  "contracts": 1,
  "orderType": "LIMIT",
  "limitPrice": 2.40,
  "timeInForce": "DAY",
  "strategyRole": "HEDGE",
  "riskReductionOnly": false,
  "dte": 79,
  "marginSnapshotId": "dms_20260623_001",
  "valuationSnapshotId": "dvs_20260623_001",
  "exposureSnapshotId": "des_20260623_001",
  "stressSnapshotId": "dst_20260623_001",
  "riskPolicyVersion": "derivative-risk-policy-v1",
  "currency": "KRW",
  "takeProfit": {
    "type": "PERCENT",
    "value": 35.0
  },
  "stopLoss": {
    "type": "PERCENT",
    "value": 25.0
  },
  "timeLimit": {
    "maxHoldingDays": 10
  },
  "source": {
    "signalId": "sig_001",
    "backtestId": "bt_001",
    "ragSourceIds": ["src_001", "src_002"]
  }
}
```

위 JSON은 `EVALUATED` 상태에서 WARN-only valuation evidence도 생성된 예시다. canonical
intent에는 반올림된 Greeks/IV를 복사하지 않고 `valuationSnapshotId`만 둔다. 화면은 해당
snapshot의 exact 값·단위·rounding metadata를 조회해 표시한다. 최초 `DRAFT` 요청에는
계약·주문 의도 필드만 필요하고 server가 계산하는 snapshot과 valuation ref는 생략한다.
caller는 6.2에서 margin snapshot을 먼저 발급받는다. `evaluate-order`는 그 snapshot의
owner/action/freshness를 검증하고 필요하면 호출자가 새 snapshot을 다시 발급받도록 거부한다.
그 뒤 owner-scoped contract master·position·quote로 exposure와 stress snapshot을 생성하고,
지원되는 European 옵션이면 별도의 WARN-only valuation snapshot을 생성해 같은 decision trace에
결속한다. valuation이 `UNSUPPORTED|ABSTAIN`이어도 hard guard가 유효하면 평가를 계속할 수 있다.

| `lifecycleState` | 필수 server-issued 근거 | 허용 동작 |
|---|---|---|
| `DRAFT` | 없음 | 후보 저장·평가 요청만 가능 |
| `EVALUATED` | `marginSnapshotId`, `exposureSnapshotId`, `stressSnapshotId`, 유효한 `riskDecision` | 검토 가능. valuation은 있으면 audit에 결속하지만 주문 권한 근거가 아님 |
| `SUBMIT_READY` | EVALUATED의 hard 근거와 만료 전 `decisionId`; 일반 submission은 one-time submission receipt, post-trade naked short call 생성·증가는 consumed step1 lineage와 미소비 final step2 receipt | KIS Mock 제출 가능 |
| `SUBMISSION_CLAIMED` | 일반 submission receipt 또는 final step2 receipt의 소비, post-trade naked short call branch의 step1 consumed-lineage, DB idempotency scope claim, active action fence, `submissionAttemptId`, `sendNotAfter` | provider outbound 후보 권한을 한 worker generation이 선점한 transient 상태. 실제 send는 first-byte 직전 CAS를 추가 통과해야 하며 expiry/renew/두 번째 send 금지 |
| `SUBMITTED` | sanitized provider acknowledgement, `orderId`, order event | intent terminal. fill/cancel 상태는 order state machine에서 추적 |
| `PENDING_RECONCILIATION` | `physicalAttemptCount=1`이고 접수 결과가 불명인 attempt와 유지 중인 active action fence | intent non-renewable. 완전한 주문·체결 대사로 해소하기 전 동일 action 재제출 금지 |
| `SUBMISSION_FAILED` | `NOT_SENT`, provider의 명확한 reject, 또는 reconciliation의 접수 부재 확정 | intent terminal. receipt/decision 재사용 금지, 재시도는 새 candidate·평가부터 시작 |
| `EXPIRED` | `EVALUATED` 또는 `SUBMIT_READY`에서 발생한 exact `terminationReason`과 이전 receipt/evidence reference | 종결 상태. provider 호출·재평가·재확인 금지, 허용 reason이면 renew가 새 DRAFT version 생성 |

client가 임의로 만든 snapshot ID나 수치는 어떤 lifecycle에서도 권한 근거가 될 수 없다.
`EXPIRED` intent를 되살리거나 `DRAFT/EVALUATED`로 되돌리지 않는다. 재시도는 새
`orderIntentId`의 DRAFT를 만들고 `supersedesOrderIntentId`로 이전 intent를 연결한다.
`SUBMISSION_CLAIMED`, `SUBMITTED`, `PENDING_RECONCILIATION`, `SUBMISSION_FAILED`는 renew
대상이 아니다. `SUBMISSION_CLAIMED` worker lease가 끝날 때
`physicalAttemptCount=0`이면 provider send 전 실패가 증명되므로
`SUBMISSION_FAILED/NOT_SENT`, count가 1이면 재전송하지 않고
`PENDING_RECONCILIATION`로 수렴한다.

`EXPIRED.terminationReason` allowlist는
`EVIDENCE_EXPIRED|RISK_LEDGER_INVALIDATED|DISCLOSURE_INVALIDATED|SAFETY_POLICY_INVALIDATED`
다. 네 reason은 모두 provider first byte 전이고 terminalization audit row가 있을 때만
renew 가능하다. client가 reason을 선택할 수 없고 server가 CAS transaction에서 기록한다.

허용 전이는 다음뿐이다.

```text
DRAFT -> EVALUATED
EVALUATED -> SUBMIT_READY | EXPIRED
SUBMIT_READY -> SUBMISSION_CLAIMED | EXPIRED
SUBMISSION_CLAIMED -> SUBMITTED | PENDING_RECONCILIATION | SUBMISSION_FAILED
PENDING_RECONCILIATION -> SUBMITTED | SUBMISSION_FAILED
```

`SUBMITTED`, `SUBMISSION_FAILED`, `EXPIRED`는 terminal이다. 목록 밖 전이는
`INVALID_TRANSITION`으로 audit하고 provider 호출을 만들지 않는다.

상태·동시성·제출 핵심 필드(비포괄):

아래 표는 lifecycle과 제출 권한에 직접 관여하는 핵심 필드다. 5.1 JSON과 각 공통 타입 표가
전체 schema를 구성하며, 이 표에 없다는 이유로 임의 optional 처리하지 않는다.

| 필드 | 필수 | 설명 |
|---|---:|---|
| `lifecycleState` | 예 | 위 lifecycle table의 8개 값. server만 전이시키며 client 요청값을 신뢰하지 않음 |
| `orderIntentId` | 예 | server-issued canonical intent ID |
| `accountId` | 예 | subject 소유의 opaque 내부 account ID. provider 계좌번호 금지 |
| `supersedesOrderIntentId` | 재평가용 새 DRAFT만 | 만료된 intent를 수정·부활시키지 않고 새 version으로 연결하는 audit reference |
| `logicalActionId` | 예 | EXPIRED renew version만 같은 ID를 유지. provider attempt가 terminal로 끝난 뒤 사용자가 의도한 새 주문은 새 ID |
| `actionGeneration` | 예 | 한 logicalAction 안의 DRAFT version generation. 최초 1, EXPIRED renew마다 증가. 새 logicalAction은 다시 1부터 시작하며 receipt는 이 값과 `issuedAt`에 결속 |
| `submissionAttemptId` | `SUBMISSION_CLAIMED` 이후 예 | receipt/idempotency/intent를 결속한 server-issued outbound attempt ID |
| `idempotencyScopeHash` | `SUBMISSION_CLAIMED` 이후 예 | raw key가 아닌 purpose/version HMAC digest. `(subject, method, routeTemplate, digest)` DB unique claim |
| `canonicalRequestHash` | `SUBMISSION_CLAIMED` 이후 예 | unique key가 아닌 payload 비교 열. 같은 idempotency scope에서 값이 다르면 `IDEMPOTENCY_CONFLICT` |
| `canonicalActionHash` | EVALUATED 이후 예 | `derivative-order-action-v1` canonical field set의 purpose/version HMAC digest. active submission fence의 비교값 |
| `confirmationViewId`, `confirmationDisclosureHash` | 일반 confirmation 이후 예 | 사용자가 실제 본 view와 loss profile·margin·exposure·stress·잔여위험의 별도 digest. active fence key로 사용 금지 |
| `confirmationViewIds`, `confirmationDisclosureHashes` | post-trade naked short call 생성·증가 confirmation 이후 예 | step1→step2 순서의 서로 다른 ID·digest 정확히 2개. singular field와 동시 사용 금지 |
| `idempotencyHashKeyVersion`, `actionHashKeyVersion`, `disclosureHashKeyVersion(s)` | 대응 hash가 있으면 예 | stored digest를 검증할 HMAC key version. naked-short 배열은 각 digest와 같은 순서. raw key material 금지, active row가 참조하는 version 조기 retire 금지 |
| `riskLedgerVersionVector` | EVALUATED 이후 예 | ordered `ACCOUNT_GLOBAL→PRODUCT_GROUP→STRATEGY` scope별 ID·generation. position+open/pending order+기존 reservation을 모든 해당 한도에 반영 |
| `riskReservationId` | `SUBMISSION_CLAIMED` 이후 예 | contracts·margin·exposure·stress capacity를 원자 예약한 server-issued ID |
| `riskReservationGeneration` | `SUBMISSION_CLAIMED` 이후 예 | stale worker와 다른 action이 같은 capacity를 중복 소비하지 못하게 하는 reservation fencing token |
| `providerOrderBaselineRef` | 자동 대사 기준이 있으면 | first-byte 전에 완료된 order/fill snapshot의 ID·asOf·provider watermark. 누락·stale이면 correlation 없는 자동 positive 대사 금지 |
| `providerWriterPolicy` | EVALUATED 이후 예 | server-controlled `EXCLUSIVE_APP_WRITER` 또는 `SHARED_EXTERNAL_WRITER`. 후자는 P2 write 전체 금지·조회 전용 |
| `sendNotAfter` | `SUBMISSION_CLAIMED` 이후 예 | receipt·decision·hard snapshot 만료시각의 최소값. first-byte 직전 재검증하는 절대 deadline |
| `attemptGeneration` | `SUBMISSION_CLAIMED` 이후 예 | stale worker를 first-byte 전에 차단하는 단조 증가 fencing token |
| `safetyGateGeneration` | `SUBMISSION_CLAIMED` 이후 예 | Kill Switch·allowlist·account·active principle/policy·hard-data invalidation을 합친 aggregate gate version. first-byte CAS에서 current row와 exact match 및 현재 허용 상태 재검증 |
| `physicalAttemptCount` | `SUBMISSION_CLAIMED` 이후 예 | 최초 transport 진입 직전 DB CAS로 `0→1`; `1` 이후 자동 재전송 금지 |
| `sendStartedAt` | `physicalAttemptCount=1`이면 예 | server clock의 transport 진입 시각. reconciliation 시간창의 기준 |
| `sendDisposition` | `SUBMISSION_CLAIMED` 이후 예 | `NOT_STARTED`, `ACKNOWLEDGED`, `AMBIGUOUS`, `NOT_SENT`, `DEFINITIVELY_REJECTED`, `CONFIRMED_NOT_ACCEPTED` |
| `orderId` | `SUBMITTED`면 예 | sanitized provider acknowledgement에 결속된 내부 order ID |
| `decisionId` | EVALUATED 이후 예 | server가 발급한 미만료 risk decision. DRAFT에서는 없음 |
| `brokerageMode`, `kisTrId` | 예 | provider wire mode와 공식 operation mapping. client override 금지 |
| `assetType` | 예 | `DOMESTIC_FUTURE` 또는 `DOMESTIC_OPTION` |
| `productGroup` | 예 | allowlist와 kill-switch 판단 단위 |
| `symbol` | 예 | KIS 상품 코드 |
| `underlyingSymbol` | 상품 master가 제공하면 예 | 노출·stress 결속용 기초자산 식별자 |
| `maturity`, `lastTradingDate`, `lastTradingAt`, `finalSettlementDate` | 예 | 최종거래·정산 경계를 분리. BSM `tau`는 server valuation 시각부터 상품 master의 `lastTradingAt`까지이며 보유기간·최종정산 시각이 아님 |
| `exerciseStyle`, `settlementType`, `contractMultiplier` | 예 | 계약 조건·정산·노출 계산 기준. BSM은 `EUROPEAN`에만 적용 |
| `strikePrice`, `optionRight` | 옵션만 예 | 선물은 `optionRight=NONE` |
| `side` | 예 | `BUY` 또는 `SELL` |
| `positionEffect` | 예 | 신규/청산/위험감소 구분 |
| `contracts` | 예 | 계약수 |
| `orderType` | 예 | 지정가 기본 |
| `limitPrice` | 항상 예 | LIMIT은 finite positive canonical decimal, MARKET은 key를 생략하지 않고 canonical `null`. MARKET의 `0` 입력은 거부 |
| `timeInForce` | 예 | v1 `DAY`; provider code와 exact mapping |
| `strategyRole` | 예 | server가 검증하는 risk-ledger STRATEGY scope |
| `riskReductionOnly` | 예 | server가 current position/open order로 재계산하는 policy 분류. client 값은 권한 근거가 아님 |
| `marginSnapshotId` | 평가 후·제출 시 예 | server가 주문 전 생성한 owner-scoped snapshot. DRAFT에서는 생략 가능 |
| `valuationSnapshotId` | 선택 | server가 발급한 `Q/THEORETICAL_PRICE` WARN-only evidence. invalid/stale/unsupported이면 생략 또는 valuation ABSTAIN이며 제출 hard gate가 아님 |
| `exposureSnapshotId`, `stressSnapshotId` | 평가 후·제출 시 예 | server가 owner position과 probability-free stress로 계산한 exposure/loss 근거. DRAFT에서는 생략 가능 |
| `dte` | 예 | Days To Expiry |
| `greeks`, `greekUnits`, `impliedVolatility` | 선택 | client 표시 hint는 untrusted. 값이 있으면 server valuation과 대조하되 주문 권한 근거가 아님 |

`accountId`는 provider 계좌번호가 아닌 opaque 내부 ID다. `positionEffect`, `riskReductionOnly`,
계약수, 가격, DTE, Greeks/IV처럼 client가 보낸 위험 관련 값은 주문 권한의 근거가 아니다.
Spring이 owner-scoped contract master, position, quote, margin, exposure, stress snapshot에서
hard 값을 다시 계산한다. hard 값 또는 ID가 불일치하면 fail-closed 한다. valuation/Greeks/IV
불일치는 해당 model evidence를 무효화하고 WARN하되 그것만으로 주문을 승인하거나 차단하지
않는다. `dte`는 UI용 정수 일수이고 BSM 입력 `timeToMaturityYears=tau`와 교환할 수 없다.

active fence용 `canonicalActionHash`의 입력은 다음 exact canonical JSON field set으로
고정한다. 숫자는 계약 schema의 canonical decimal string으로 정규화하고 key 순서·UTF-8
직렬화 규칙을 version에 포함한다. high-level intent와 KIS provider-wire projection을 함께
결속해 mapping 변경이 같은 action으로 보이지 않게 한다.

```text
schemaVersion=derivative-order-action-v1
providerMappingVersion=KIS_DOMESTIC_FUTUREOPTION_ORDER_V1,
brokerageMode, accountId, kisTrId,
ordPrcsDvsnCd="02",
symbol, shtnPdno=symbol,
side, sllBuyDvsnCd(BUY="02"|SELL="01"),
contracts, ordQty=canonical positive integer string,
orderType, limitPrice,
unitPrice(LIMIT=canonical price string|MARKET="0"),
nmprTypeCd(LIMIT="01"|MARKET="02"),
krxNmprCndtCd="0",
ordDvsnCd(LIMIT="01"|MARKET="02"),
timeInForce="DAY", ctacTlno="", fuopItemDvsnCd=""
```

`contracts`는 bool을 제외한 양의 정수다. LIMIT의 `limitPrice`는 finite positive canonical
decimal이고 MARKET은 `limitPrice=null` sentinel을 항상 포함한다. MARKET의 omitted/`0`,
LIMIT의 null/omitted는 schema 오류다. `timeInForce`는 v1에서 exact `DAY`이고 IOC/FOK,
조건부·최유리 code는 `CANDIDATE_DISABLED`다. raw `CANO/ACNT_PRDT_CD`는 secret boundary의
provider credential mapping이며 hash나 문서·event에 넣지 않고 opaque `accountId`로만
결속한다. 위 projection과 일치하지 않는 code 또는 mapping version은 fail-closed한다.

`assetType`, `productGroup`, maturity/expiry/strike/right, `positionEffect`,
`riskReductionOnly`, strategy/model label, `orderIntentId`, `logicalActionId`,
`actionGeneration`, `decisionId`, receipt/idempotency key, request/timestamp, quote, margin,
exposure, stress, loss profile, model evidence는 이 hash에서 제외한다. contract/policy/risk
분류는 server contract master와 reservation에서 별도 검증한다. 따라서 같은 physical
provider payload는 재평가로 동적 evidence가 바뀌어도 같은 fence에 걸리고,
symbol·side·contracts·orderType·정규화 가격·TIF 중 하나가 바뀌면 다른 action이다.
사용자에게 표시한 position effect·risk-reduction 분류·loss profile·margin·exposure·
stress·잔여위험은 별도 `confirmationDisclosureHash`에 결속한다.

`confirmationDisclosureHash`는 다음 `derivative-confirmation-disclosure-v1` canonical
object의 purpose/version HMAC digest다. map key와 scenario/leg 배열 정렬, decimal·통화·
timestamp 정규화 규칙도 schema version에 포함한다.

```text
schemaVersion, hashKeyVersion,
confirmationPurpose(GENERAL_SUBMISSION|OPTION_SHORT),
confirmationStep(1|2; GENERAL_SUBMISSION은 1만 허용),
ownerUserId, accountId, orderIntentId, logicalActionId, actionGeneration,
canonicalActionHash,
decisionId, principleId, principleVersion, decisionValidUntil,
riskLedgerVersionVector, positionStructureHash,
positionEffect, riskReductionOnly,
signedPositionContractsBefore, signedPositionContractsAfter,
coverageType(NOT_APPLICABLE|NAKED),
lossProfile, maxLossAmount, maxLossCurrency,
contracts, contractMultiplier, premiumCashflow, estimatedFees,
currency, exposureUnit,
marketValueApplicability(APPLICABLE|NOT_APPLICABLE),
disclosureTemplateId, disclosureTemplateVersion, disclosureTemplateContentHash, locale,
marginSnapshot{id, asOf, expiresAt, availableMarginCollateral,
               requiredMarginEstimate, marginBufferRate, marginBufferAmount,
               providerEvidenceHash, normalizationFormulaVersion},
exposureSnapshot{id, asOf, expiresAt,
                 positionStructureHash, sourceHash,
                 grossContractNotional, conservativeDeltaEquivalentExposure,
                 conservativeRiskDelta, riskDeltaMethod,
                 riskDeltaContractSourceHash,
                 grossCurrentMarketValue, marketValueCurrency, marketValueAsOf},
stressSnapshot{id, asOf, expiresAt, sourceHash,
               orderedScenarios[{
                 scenarioId, outputKind, probability=null,
                 result{kind=LOSS_AMOUNT|MARGIN_SHORTFALL, amount, currency},
                 valuationMethod, formulaVersion, inputHash, sourceHash
               }]},
sortedResidualRiskCodes
```

각 stress result는 tagged oneOf다. 손익은 `kind=LOSS_AMOUNT`, margin 부족은
`kind=MARGIN_SHORTFALL`이고 amount는 finite non-negative이며 currency가 필수다. 다른 kind의
field, `probability` 생략/non-null, `lossAmount=null`처럼 null로 다른 variant를 흉내 낸
표현은 허용하지 않는다. snapshot ID만 같아도 값·`asOf/expiresAt`·source/formula hash,
position effect·risk-reduction 분류, signed before/after position, market-value
applicability, 화면 template/version/content hash/locale, active principle version 또는 risk-ledger
version vector가 바뀌면 기존 receipt는 무효다. 새 disclosure object를 화면에 표시하고 새
receipt를 발급해야 한다. 이 hash는 사용자가 본 설명을 증명할 뿐 provider 중복 방지 key나
주문 허용 근거를 대신하지 않는다.

canonical projection mapping은
`marginSnapshot.providerEvidenceHash := derivative_margin_snapshot.providerEvidence.payloadHash`,
`marginSnapshot.normalizationFormulaVersion := derivative_margin_snapshot.normalization.formulaVersion`,
`exposureSnapshot.positionStructureHash/sourceHash := derivative_exposure_and_stress_snapshot`의
동명 필드, `stressSnapshot.sourceHash := derivative_exposure_and_stress_snapshot.sourceHash`의
exact copy다. 같은 ID에서 이 mapping 값이 다르면 view 발급은 0건이다.

`OPTION_SHORT`의 step1과 step2는 같은 view나 hash를 재사용하지 않는다. step1 확인이
성공한 transaction에서 `confirmationStep=2`인 새 disclosure와 새
`confirmationViewId`를 발급한다. 두 단계가 같은 손실·margin·stress 값을 보여 주더라도
`confirmationStep`, step별 prompt/template와 view ID가 달라 별도 challenge가 된다.
각 `(disclosureTemplateId, disclosureTemplateVersion, locale)`의 렌더링 내용은 immutable
publish artifact이고 `disclosureTemplateContentHash`로 pin한다. 같은 version의 문구를
덮어쓰지 않으며 content hash 불일치는 view 발급 전 fail-closed한다.

각 HMAC digest에는 `hashKeyVersion`을 함께 저장한다. key rotation 중에도
`SUBMISSION_CLAIMED|PENDING_RECONCILIATION`과 미소비 receipt/idempotency row가 참조하는
version은 retire하지 않는다. 신규 제출은 active fence를 모든 non-retired key version으로
비교하고 stored version으로 replay를 검증한다. rotation으로 동일 normalized action이 새
digest를 얻어 fence를 우회하거나 raw key가 저장되는 경우는 허용하지 않는다.

### 5.2 derivative_margin_snapshot

```json
{
  "schema": "derivative_margin_snapshot",
  "marginSnapshotId": "dms_20260623_001",
  "accountId": "mock-derivatives-01",
  "orderIntentId": "doi_20260623_001",
  "brokerageMode": "KIS_DERIVATIVE_MOCK",
  "session": "DAY",
  "riskLedgerVersionVector": [
    {"scope": "ACCOUNT_GLOBAL", "scopeId": "mock-derivatives-01", "generation": 42},
    {"scope": "PRODUCT_GROUP", "scopeId": "KOSPI200_OPTION", "generation": 17},
    {"scope": "STRATEGY", "scopeId": "HEDGE", "generation": 9}
  ],
  "kisTrId": "VTTO5105R",
  "currency": "KRW",
  "orderableContracts": 3,
  "liquidatableContracts": 1,
  "orderableCash": 7200000,
  "availableMarginCollateral": 7200000,
  "requiredMarginEstimate": 600000,
  "additionalMarginEstimate": 0,
  "marginBufferRate": 0.2,
  "marginBufferAmount": 120000,
  "marginBufferOk": true,
  "providerEvidence": {
    "orderableCash": 7200000,
    "orderableContracts": 3,
    "totalOrderableContracts": 3,
    "liquidatableContracts": 1,
    "payloadHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  },
  "normalization": {
    "availableMarginCollateralSource": "providerEvidence.orderableCash",
    "formulaVersion": "margin-collateral-v1"
  },
  "asOf": "2026-06-23T10:15:22+09:00",
  "expiresAt": "2026-06-23T10:16:22+09:00"
}
```

### 5.2.1 derivative_valuation_snapshot

이 schema는 P2 계획 계약이며 P1 public contracts에는 아직 추가하지 않는다.

> **KNOWN_UPSTREAM_ERRATUM / ACTIVATION NO-GO**
>
> 상위 권위의 `docs/API_명세서.md`에 남아 있는 기존 BSM 예시 수치
> (`call=2315.42`, `d1=-0.0941`, `d2=-0.2341`)는 같은 입력의 q 포함식과 일치하지 않는다.
> 이 P2 fixture는 독립 oracle로 검증한 correction candidate일 뿐 상위 계약을 몰래
> override하지 않는다. 별도 승인으로 상위 예시와 Greeks/IV 단위를 교정하기 전에는 S6.4와
> P2 valuation activation이 `NO-GO`다.

```json
{
  "schema": "derivative_valuation_snapshot",
  "valuationSnapshotId": "dvs_20260623_001",
  "symbol": "201V6300",
  "model": "BSM_EUROPEAN_CONTINUOUS_DIVIDEND",
  "status": "AVAILABLE",
  "outputKind": "THEORETICAL_PRICE",
  "measure": "Q",
  "theoreticalPrice": 2.40,
  "inputs": {
    "spot": 355.25,
    "strike": 310.0,
    "timeToMaturityYears": 0.2170180111618468,
    "riskFreeRate": 0.032,
    "dividendYield": 0.018,
    "volatility": 0.2574591676840513
  },
  "inputProvenance": {
    "spotQuote": {
      "quoteId": "dq_20260623_001",
      "price": 355.25,
      "priceType": "MID",
      "sourceType": "PROJECT_FIXTURE",
      "asOf": "2026-06-23T10:15:20+09:00"
    },
    "optionMarketQuote": {
      "quoteId": "doq_20260623_001",
      "bid": 2.35,
      "ask": 2.45,
      "mid": 2.40,
      "calibrationPrice": 2.40,
      "priceType": "MID",
      "sourceType": "PROJECT_FIXTURE",
      "asOf": "2026-06-23T10:15:20+09:00"
    },
    "contractMasterId": "dcm_201V6300",
    "riskFreeRate": {
      "sourceId": "src_rate_001",
      "value": 0.032,
      "tenorDays": 79,
      "compounding": "CONTINUOUS",
      "sourceType": "PROJECT_FIXTURE"
    },
    "dividendYield": {
      "sourceId": "src_dividend_001",
      "value": 0.018,
      "compounding": "CONTINUOUS",
      "sourceType": "PROJECT_FIXTURE"
    },
    "volatilityEstimator": "IMPLIED_VOLATILITY_BISECTION",
    "volatilityWindow": null,
    "solver": {
      "lowerVolatility": 0.0001,
      "upperVolatility": 5.0,
      "tolerancePrice": 1e-10,
      "iterations": 40,
      "recomputedPrice": 2.4000000000000057,
      "pricingError": 5.773159728050814e-15
    },
    "asOf": "2026-06-23T10:15:20+09:00"
  },
  "greeks": {
    "delta": -0.1105532537,
    "gamma": 0.0044241655,
    "vegaPerOnePctPoint": 0.3119634212,
    "thetaPerCalendarDay": -0.0489814873,
    "rhoPerOnePctPoint": -0.0904401801
  },
  "assumptions": [
    "EUROPEAN_EXERCISE",
    "CONTINUOUS_TRADING_AND_REBALANCING",
    "FRICTIONLESS_MARKET",
    "CONSTANT_R_Q_SIGMA",
    "LOGNORMAL_DIFFUSION",
    "NO_JUMPS_OR_GAPS"
  ],
  "validationStatus": "WARN",
  "calibrationStatus": "NOT_APPLICABLE",
  "ivInversionStatus": "CALIBRATED_TO_INPUT_QUOTE",
  "warningCodes": [
    "CALIBRATION_IDENTITY",
    "MODEL_ASSUMPTIONS_LIMITED"
  ],
  "warnings": [
    "위험중립 Q 이론가이며 실제 상승확률이 아닙니다.",
    "동일 market quote로 IV를 역산한 뒤 같은 BSM 식으로 재가격한 값이므로 독립 fair-value 검증이 아닙니다.",
    "이산 헤지에는 gamma/vega, jump/gap, 거래비용·유동성 잔여위험이 있습니다."
  ],
  "modelVersion": "bsm-q-dividend-v1",
  "dataVersion": "quote-20260623T101520+0900",
  "sourceHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "asOf": "2026-06-23T10:15:20+09:00",
  "expiresAt": "2026-06-23T10:16:20+09:00"
}
```

canonical 식은 연속배당수익률 `q`를 포함한다.

```text
d1 = (ln(S/K) + (r - q + sigma^2/2) * tau) / (sigma * sqrt(tau))
d2 = d1 - sigma * sqrt(tau)
Call = S*exp(-q*tau)*N(d1) - K*exp(-r*tau)*N(d2)
Put  = K*exp(-r*tau)*N(-d2) - S*exp(-q*tau)*N(-d1)
```

`tau`는 valuation 시점부터 contract master의 최종거래일 세션 종료
`lastTradingAt`까지 `ACT/365F`로 계산한 연 단위 시간이다. KOSPI200 옵션 fixture의
최종거래일 종료는 일반일 15:45가 아닌 15:20이다. 투자자의 보유기간·백테스트 기간·
`finalSettlementDate`까지의 시간이 아니다. `N(d1)`·`N(d2)`는 위험중립 가격식의 CDF
값이며 실제 시장 상승확률로 표시하지 않는다. `q=0`은 canonical 식의 특수한 경우다.

BSM/Greeks/IV 계산 domain은 finite `S>0`, `K>0`, `tau>0`, `sigma>0`이다. `tau=0`,
`sigma=0`, 음수 또는 non-finite 입력은 식을 억지로 piecewise 확장하지 않고
`status=ABSTAIN`, `validationStatus=FAIL`, `failureCode=VALUATION_DOMAIN_INVALID`로
반환하며 calculation/Greeks/IV key를 모두 생략한다. 만기 payoff 표시는 별도 payoff
calculator의 책임이고 BSM Greek으로 위장하지 않는다.

`ACT/365F`, Vega/Rho의 `/100`, calendar Theta의 `/365`는 이 프로젝트의 wire·fixture
convention이다. 시장 전체의 유일한 표준이라고 주장하지 않으며 model/source manifest에
`PROJECT_CONVENTION`으로 기록한다.

Vega와 Rho는 내부적으로 변동성·금리 `1.00` 변화 기준으로 계산하고 wire 값은 `/100`한
1%p 기준이다. 표준 market theta는 calendar time이 하루 흐를 때의 가격 변화로 정의하므로
`Theta_calendar=dV/dt_calendar=-dV/dtau`,
`thetaPerCalendarDay=thetaPerYear/365`이며 보통 long option에서 음수다. 잔존만기 `tau`에
대한 편미분을 직접 노출하면 부호가 반대가 될 수 있으므로 `dV/dTau`를 theta라고 부르지 않는다.

IV bisection 전 European 무차익 경계를 확인한다.

```text
Call: max(0, S*exp(-q*tau)-K*exp(-r*tau)) <= price <= S*exp(-q*tau)
Put : max(0, K*exp(-r*tau)-S*exp(-q*tau)) <= price <= K*exp(-r*tau)
```

경계 밖 또는 volatility bracket `[0.0001, 5.0]`에서 재현 불가능하면 solver 반복 없이
`IV_NOT_BRACKETED`이고 `status=ABSTAIN`, `validationStatus=FAIL`이다. exercise style이
European이 아니면 BSM을 적용하지 않고 `status=UNSUPPORTED`,
`validationStatus=NOT_APPLICABLE`, reason과 contract reference만 남긴다. European이지만
contract master가 불완전하면 `status=ABSTAIN`, `validationStatus=FAIL`이다.

IV에는 사용한 option quote의 bid/ask/mid, 선택한 calibration price·type·`asOf`, solver
bracket/tolerance/iteration/pricing error, `r/q`의 tenor·compounding·source를 함께 남긴다.
같은 quote에서 IV를 역산하고 같은 식으로 재가격한 round-trip은 수치 구현 검증이지 독립적인
공정가치 검증이 아니므로 `calibrationStatus=NOT_APPLICABLE`,
`ivInversionStatus=CALIBRATED_TO_INPUT_QUOTE`, `CALIBRATION_IDENTITY` warning으로
구분해 기록한다.

### 5.2.2 long put exposure와 deterministic stress

아래 snapshot은 5.1·5.2.1의 보호적 put 예시와 같은 order intent다.

```json
{
  "schema": "derivative_exposure_and_stress_snapshot",
  "exposureSnapshotId": "des_20260623_001",
  "stressSnapshotId": "dst_20260623_001",
  "orderIntentId": "doi_20260623_001",
  "marginSnapshotId": "dms_20260623_001",
  "riskLedgerVersionVector": [
    {"scope": "ACCOUNT_GLOBAL", "scopeId": "mock-derivatives-01", "generation": 42},
    {"scope": "PRODUCT_GROUP", "scopeId": "KOSPI200_OPTION", "generation": 17},
    {"scope": "STRATEGY", "scopeId": "HEDGE", "generation": 9}
  ],
  "contracts": 1,
  "signedPositionContractsBefore": 0,
  "signedPositionContractsAfter": 1,
  "positionEffect": "OPEN",
  "riskReductionOnly": false,
  "positionStructureHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "contractMultiplier": 250000,
  "currency": "KRW",
  "exposureUnit": "KRW_NOTIONAL",
  "referenceSpot": 355.25,
  "strikePrice": 310.0,
  "optionRight": "PUT",
  "conservativeRiskDelta": -1.0,
  "riskDeltaMethod": "CONSERVATIVE_CONTRACT_BOUND_V1",
  "riskDeltaContractSourceHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "referenceOptionPrice": 2.40,
  "marketValueApplicability": "APPLICABLE",
  "marketValueCurrency": "KRW",
  "marketValueAsOf": "2026-06-23T10:15:20+09:00",
  "side": "BUY",
  "positionStructure": "LONG_PUT",
  "coverageType": "NOT_APPLICABLE",
  "lossProfile": "BOUNDED",
  "premiumCashflow": -600000,
  "estimatedFees": 15000,
  "maxLossAmount": 615000,
  "maxLossCurrency": "KRW",
  "grossContractNotional": 88812500,
  "grossCurrentMarketValue": 600000,
  "conservativeDeltaEquivalentExposure": -88812500,
  "requiredMarginEstimate": 600000,
  "stressScenarios": [
    {
      "scenarioId": "OPTION_EXPIRES_WORTHLESS",
      "inputHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "outputKind": "DETERMINISTIC_STRESS",
      "measure": "NOT_APPLICABLE",
      "probability": null,
      "metric": "LOSS_AMOUNT",
      "metricCurrency": "KRW",
      "valuationMethod": "PREMIUM_PLUS_ESTIMATED_FEES_V1",
      "premiumPaid": 600000,
      "estimatedFees": 15000,
      "lossAmount": 615000,
      "formulaVersion": "long-option-loss-bound-v1",
      "sourceHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  ],
  "residualRiskCodes": [
    "DISCRETE_HEDGE_RISK",
    "GAP_RISK",
    "LIQUIDITY_RISK",
    "MODEL_RISK"
  ],
  "sourceHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "asOf": "2026-06-23T10:15:22+09:00",
  "expiresAt": "2026-06-23T10:16:22+09:00"
}
```

### 5.2.3 independent naked short call stress

아래는 앞의 long put과 결합하거나 netting하지 않는 독립적인 naked short call 예시다.
먼저 같은 owner/action에 결속된 short 전용 margin snapshot을 생성한다.

```json
{
  "schema": "derivative_margin_snapshot",
  "marginSnapshotId": "dms_short_20260623_001",
  "accountId": "mock-derivatives-01",
  "orderIntentId": "doi_short_20260623_001",
  "brokerageMode": "KIS_DERIVATIVE_MOCK",
  "session": "DAY",
  "riskLedgerVersionVector": [
    {"scope": "ACCOUNT_GLOBAL", "scopeId": "mock-derivatives-01", "generation": 42},
    {"scope": "PRODUCT_GROUP", "scopeId": "KOSPI200_OPTION", "generation": 17},
    {"scope": "STRATEGY", "scopeId": "OPTION_SHORT", "generation": 5}
  ],
  "kisTrId": "VTTO5105R",
  "currency": "KRW",
  "orderableContracts": 1,
  "liquidatableContracts": 0,
  "orderableCash": 7200000,
  "availableMarginCollateral": 7200000,
  "requiredMarginEstimate": 4100000,
  "additionalMarginEstimate": 3500000,
  "marginBufferRate": 0.2,
  "marginBufferAmount": 820000,
  "marginBufferOk": true,
  "providerEvidence": {
    "orderableCash": 7200000,
    "orderableContracts": 1,
    "totalOrderableContracts": 1,
    "liquidatableContracts": 0,
    "payloadHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  },
  "normalization": {
    "availableMarginCollateralSource": "providerEvidence.orderableCash",
    "formulaVersion": "margin-collateral-v1"
  },
  "asOf": "2026-06-23T10:15:22+09:00",
  "expiresAt": "2026-06-23T10:16:22+09:00"
}
```

```json
{
  "schema": "derivative_exposure_and_stress_snapshot",
  "exposureSnapshotId": "des_short_20260623_001",
  "stressSnapshotId": "dst_short_20260623_001",
  "orderIntentId": "doi_short_20260623_001",
  "marginSnapshotId": "dms_short_20260623_001",
  "riskLedgerVersionVector": [
    {"scope": "ACCOUNT_GLOBAL", "scopeId": "mock-derivatives-01", "generation": 42},
    {"scope": "PRODUCT_GROUP", "scopeId": "KOSPI200_OPTION", "generation": 17},
    {"scope": "STRATEGY", "scopeId": "OPTION_SHORT", "generation": 5}
  ],
  "contracts": 1,
  "signedPositionContractsBefore": 0,
  "signedPositionContractsAfter": -1,
  "positionEffect": "OPEN",
  "riskReductionOnly": false,
  "positionStructureHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "contractMultiplier": 250000,
  "currency": "KRW",
  "exposureUnit": "KRW_NOTIONAL",
  "referenceSpot": 360.0,
  "strikePrice": 360.0,
  "optionRight": "CALL",
  "conservativeRiskDelta": 1.0,
  "riskDeltaMethod": "CONSERVATIVE_CONTRACT_BOUND_V1",
  "riskDeltaContractSourceHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "referenceOptionPrice": 2.40,
  "marketValueApplicability": "APPLICABLE",
  "marketValueCurrency": "KRW",
  "marketValueAsOf": "2026-06-23T10:15:20+09:00",
  "side": "SELL",
  "positionStructure": "NAKED_SHORT_CALL",
  "coverageType": "NAKED",
  "lossProfile": "UNBOUNDED",
  "maxLossAmount": null,
  "maxLossCurrency": null,
  "premiumCashflow": 600000,
  "estimatedFees": 15000,
  "grossContractNotional": 90000000,
  "grossCurrentMarketValue": 600000,
  "conservativeDeltaEquivalentExposure": -90000000,
  "requiredMarginEstimate": 4100000,
  "stressScenarios": [
    {
      "scenarioId": "TERMINAL_EXPIRY_UNDERLYING_UP_10_PERCENT",
      "inputHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "outputKind": "DETERMINISTIC_STRESS",
      "measure": "NOT_APPLICABLE",
      "probability": null,
      "metric": "LOSS_AMOUNT",
      "metricCurrency": "KRW",
      "valuationMethod": "EXPIRY_INTRINSIC_NET_PREMIUM_PLUS_COST_BUFFER_V1",
      "horizon": "EXPIRY",
      "shockedUnderlyingSpot": 396.0,
      "intrinsicLiability": 9000000,
      "premiumOffset": 600000,
      "baseSpreadSlippageCost": 250000,
      "stressedSpreadSlippageCost": 500000,
      "estimatedFees": 15000,
      "costBuffer": 515000,
      "costBufferIncludesEstimatedFees": true,
      "lossAmount": 8915000,
      "formulaVersion": "short-call-gap-stress-v1",
      "sourceHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    },
    {
      "scenarioId": "IMPLIED_VOLATILITY_DOUBLES_MODEL_STRESS",
      "inputHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "outputKind": "DETERMINISTIC_STRESS",
      "measure": "Q",
      "probability": null,
      "metric": "LOSS_AMOUNT",
      "metricCurrency": "KRW",
      "policyAuthority": "WARN_ONLY",
      "valuationMethod": "BSM_Q_REPRICING_V1",
      "valuationBasis": "INCREMENTAL_MODEL_PRICE_CHANGE",
      "spot": 360.0,
      "strike": 360.0,
      "timeToMaturityYears": 0.2170180111618468,
      "riskFreeRate": 0.032,
      "dividendYield": 0.018,
      "baselineVolatility": 0.25,
      "shockedVolatility": 0.50,
      "baselineModelPrice": 17.176020090976237,
      "shockedModelPrice": 33.74335558075603,
      "contractMultiplier": 250000,
      "unroundedLossAmount": 4141833.872444948,
      "lossAmount": 4141834,
      "monetaryRounding": "HALF_EVEN_TO_KRW",
      "formulaVersion": "short-call-volatility-model-stress-v1",
      "sourceHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    },
    {
      "scenarioId": "MARGIN_REQUIREMENT_DOUBLES",
      "inputHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "outputKind": "DETERMINISTIC_STRESS",
      "measure": "NOT_APPLICABLE",
      "metric": "MARGIN_SHORTFALL",
      "metricCurrency": "KRW",
      "probability": null,
      "valuationMethod": "MARGIN_LEDGER_DELTA_V1",
      "baselineRequiredMargin": 4100000,
      "shockedRequiredMargin": 8200000,
      "availableMarginCollateral": 7200000,
      "collateralSourceField": "derivative_margin_snapshot.availableMarginCollateral",
      "marginShortfall": 1000000,
      "formulaVersion": "margin-shortfall-stress-v1",
      "sourceHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  ],
  "residualRiskCodes": [
    "ASSIGNMENT_SETTLEMENT_RISK",
    "DISCRETE_HEDGE_RISK",
    "GAMMA_VEGA_RISK",
    "GAP_RISK",
    "LIQUIDITY_CROWDING_RISK",
    "MODEL_RISK",
    "UNBOUNDED_LOSS_RISK"
  ],
  "sourceHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "asOf": "2026-06-23T10:15:22+09:00",
  "expiresAt": "2026-06-23T10:16:22+09:00"
}
```

`probability`는 deterministic stress에서 필수 `null`이다. 선택한 충격의 `lossAmount`는
최대손실이 아니고, `MARGIN_SHORTFALL`은 손익과 다른 variant이므로 `lossAmount` key를
싣지 않는다. disclosure canonicalizer는 두 variant를 tagged result로 변환한다.
server는 양수인 client `contracts`, `side`, 현재 owner-scoped position, 모든 option leg,
contract multiplier, assignment/settlement 조건으로 `signedPositionContractsAfter`를
재구성한 뒤 snapshot을 발급한다. margin 관련 stress 입력은 같은 owner/action에 bind된
`marginSnapshotId`의 `availableMarginCollateral/asOf/expiresAt/normalization`에서만
가져온다. v1 fixture의 `availableMarginCollateral`은
`providerEvidence.orderableCash`를 `margin-collateral-v1`로 1:1 정규화한 값이다. provider
필드 의미가 이 계약과 일치하지 않거나 source field가 누락되면 값을 추측하지 않고 margin
snapshot 생성을 실패시킨다. snapshot이 stale하거나 binding이 다르면 stress를 계산하지 않고
hard guard를 실패시킨다.

이 JSON은 terminal gap·spread/slippage×2·margin hard stress와 optional vol×2 model
repricing의 독립 수치 fixture다. 실제 post-trade short 노출 제출용 `stressSnapshot`의
mandatory hard set은 versioned terminal gap, spread/slippage, leverage/margin, fat-tail
proxy, volatility-cluster, crisis-correlation, liquidity/crowding scenario다. 해당 set의
source/input hash가 없으면 `deterministicStressOk=true`가 될 수 없다. Q 기반 vol repricing은
있으면 WARN-only이고 누락 자체는 hard block이 아니며 다른 probability-free hard stress를
대신하지 않는다.

```text
grossContractNotional
  = abs(signedPositionContractsAfter) * contractMultiplier * referenceSpot

conservativeDeltaEquivalentExposure
  = signedPositionContractsAfter * contractMultiplier * referenceSpot * conservativeRiskDelta

grossCurrentMarketValue
  = abs(signedPositionContractsAfter) * contractMultiplier * referenceOptionPrice
```

P2 v1은 single-leg만 허용한다. `conservativeRiskDelta`는 contract master의 option right로
call `+1`, put `-1`을 server가 정하는 절대상한이며 WARN-only BSM delta를 복사하지 않는다.
이 exposure는 premium, margin, market value, bounded max loss를 대신하지 않는다.
`grossCurrentMarketValue`는 option mark의 gross absolute value이며
`marketValueCurrency/marketValueAsOf`와 함께 기록한다.

### 5.2.4 선물·옵션 instrument profile `oneOf`

`derivative_exposure_and_stress_snapshot`과 confirmation disclosure는 asset type에 따라 다음
두 profile 중 정확히 하나를 따른다.

| 필드 | `DOMESTIC_FUTURE` | `DOMESTIC_OPTION` |
|---|---|---|
| `optionRight` | `NONE` | `CALL|PUT` |
| `strikePrice`, `referenceOptionPrice` | `null` | finite, contract/quote-bound |
| `conservativeRiskDelta` | `null` | call=`+1.0`, put=`-1.0`; `CONSERVATIVE_CONTRACT_BOUND_V1` |
| `riskDeltaMethod`, `riskDeltaContractSourceHash` | 둘 다 `null` | exact method와 64-hex contract source hash |
| `linearDelta` | `1.0`; signed direction은 `signedPositionContractsAfter`가 담당 | `null` |
| `positionStructure` | `FUTURE_LONG|FUTURE_SHORT` | exact v1 `LONG_CALL|LONG_PUT|NAKED_SHORT_CALL` |
| `coverageType` | `NOT_APPLICABLE` | long option=`NOT_APPLICABLE`; naked short call=`NAKED` |
| `lossProfile` | `LINEAR_MARGIN_DEPENDENT` | long=`BOUNDED`; naked short call=`UNBOUNDED` |
| `premiumCashflow` | `null` | signed premium cashflow |
| `estimatedFees` | finite KRW | finite KRW |
| `maxLossAmount`, `maxLossCurrency` | 둘 다 `null`; margin은 최대손실이 아님 | bounded long은 둘 다 finite/`KRW`; unbounded short call은 둘 다 `null` |
| `grossContractNotional` | `abs(contracts)*multiplier*referenceFuturesPrice` | 위 option gross-notional 식 |
| `grossCurrentMarketValue`, `marketValueCurrency`, `marketValueAsOf` | 모두 `null`, `marketValueApplicability=NOT_APPLICABLE` | 모두 finite/non-null, `marketValueApplicability=APPLICABLE` |
| `conservativeDeltaEquivalentExposure` | `signedContracts*multiplier*referenceFuturesPrice*linearDelta` | 위 conservative option delta 식 |
| `currency`, `exposureUnit` | `KRW`, `KRW_NOTIONAL` | `KRW`, `KRW_NOTIONAL` |
| `positionStructureHash`, `asOf`, `expiresAt`, `sourceHash` | 모두 non-null | 모두 non-null |
| stress | 방향별 gap, spread/slippage, margin×2; probability `null` | option gap/vol/spread/margin scenario; probability `null` |

표의 field는 profile 식별에 필요한 고정 shape다. 각 profile에서 `null`로 명시한 key는
생략할 수 없고, 반대로 v1 schema에 없는 unknown key는 거부한다. `null`·omitted·`0`은
상호 교환하지 않는다.
선물의 direction-specific `stressLossSide`는 long이면 `DOWN`, short이면 `UP`이고, margin
shortfall와 손익 stress를 분리한다. 선물 margin·variation margin을 premium이나
`maxLossAmount`로 직렬화하지 않는다. profile과 맞지 않는 field 조합은 schema 오류이며
`null`/`NOT_APPLICABLE`을 임의의 0으로 바꾸지 않는다.
`NAKED_SHORT_PUT|COVERED_CALL|SPREAD`와 기타 multi-leg는 exact leg/coverage/max-loss
계약이 승인되기 전 `CANDIDATE_DISABLED`다.
P2 account activation은 authoritative complete position scan에서 이 unsupported shape가
0건이어야 한다. 활성화 뒤 발견되면 자동 close shape를 추측하지 않고 모든 P2 write를 HOLD해
operator incident로 전환한다. 외부 정리는 `EXCLUSIVE_APP_WRITER` 해제 뒤 수행하고 flat
baseline과 새 attestation 전에는 재활성화하지 않는다.

### 5.3 derivative_risk_decision

```json
{
  "schema": "derivative_risk_decision",
  "decisionId": "dec_deriv_20260623_001",
  "orderIntentId": "doi_20260623_001",
  "marginSnapshotId": "dms_20260623_001",
  "exposureSnapshotId": "des_20260623_001",
  "stressSnapshotId": "dst_20260623_001",
  "principleId": "prc_001",
  "principleVersion": 1,
  "riskLedgerVersionVector": [
    {"scope": "ACCOUNT_GLOBAL", "scopeId": "mock-derivatives-01", "generation": 42},
    {"scope": "PRODUCT_GROUP", "scopeId": "KOSPI200_OPTION", "generation": 17},
    {"scope": "STRATEGY", "scopeId": "HEDGE", "generation": 9}
  ],
  "lifecycleState": "EVALUATED",
  "decision": "HOLD",
  "reasons": [
    "synthetic contract master fixture는 provider orderable이 아님",
    "옵션 매수 premium이 전략별 한도 안에 있음",
    "deterministic maxContracts 정책이 신규 방향성 주문을 1계약으로 제한",
    "causal HMM RISK_OFF posterior는 주문 권한 없는 보조 WARN context"
  ],
  "guards": {
    "productGroupAllowed": true,
    "contractMasterOrderable": false,
    "productKillSwitchActive": false,
    "sessionAllowed": true,
    "orderableQuantityOk": true,
    "marginBufferOk": true,
    "maxContractsOk": true,
    "orderTypeAllowed": true,
    "optionShortConfirmationRequired": false,
    "liquidityOk": true,
    "dataFreshnessOk": true,
    "modelEvidenceAvailableForExplanation": true,
    "lossProfileKnown": true,
    "deterministicStressOk": true,
    "dailyLossOk": true,
    "cooldownOk": true
  },
  "limits": {
    "currency": "KRW",
    "maxContracts": 1,
    "dailyLossLimit": 1000000,
    "maxPremium": 750000,
    "maxDte": 120
  },
  "modelEvidence": {
    "valuation": {
      "status": "AVAILABLE",
      "valuationSnapshotId": "dvs_20260623_001",
      "validationStatus": "WARN",
      "orderAuthority": "WARN_ONLY"
    },
    "hmm": {
      "status": "AVAILABLE",
      "regimeEvidenceId": "hme_20260623_001",
      "nativeState": "RISK_OFF",
      "causalPosterior": 0.71,
      "normalizedEntropy": 0.868721,
      "validationStatus": "PASS",
      "diagnosticWarnings": [],
      "orderAuthority": "WARN_ONLY"
    }
  },
  "exposureSummary": {
    "exposureSnapshotId": "des_20260623_001",
    "contracts": 1,
    "signedPositionContractsAfter": 1,
    "contractMultiplier": 250000,
    "currency": "KRW",
    "exposureUnit": "KRW_NOTIONAL",
    "lossProfile": "BOUNDED",
    "premiumCashflow": -600000,
    "estimatedFees": 15000,
    "maxLossAmount": 615000,
    "grossContractNotional": 88812500,
    "grossCurrentMarketValue": 600000,
    "marketValueCurrency": "KRW",
    "marketValueAsOf": "2026-06-23T10:15:20+09:00",
    "conservativeRiskDelta": -1.0,
    "riskDeltaMethod": "CONSERVATIVE_CONTRACT_BOUND_V1",
    "conservativeDeltaEquivalentExposure": -88812500,
    "requiredMarginEstimate": 600000,
    "asOf": "2026-06-23T10:15:22+09:00",
    "sourceHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  },
  "stressSummary": {
    "stressSnapshotId": "dst_20260623_001",
    "probabilityPolicy": "REQUIRED_NULL",
    "currency": "KRW",
    "worstConfiguredStressLoss": 615000,
    "limitExceeded": false
  },
  "asOf": "2026-06-23T10:15:22+09:00",
  "sourceHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "validUntil": "2026-06-23T10:17:20+09:00",
  "nextAction": "NONE"
}
```

모델 evidence의 `orderAuthority=WARN_ONLY`는
`modelEvidenceAvailableForExplanation=true`여도 바뀌지 않는다.
production-valid `nextAction=CONFIRM_SUBMISSION`도 provider 제출 권한이 아니라 6.4.1 또는
6.4.2의 확인 receipt 발급 단계로만 이동할 수 있다는 뜻이다. 위 synthetic fixture는
`decision=HOLD`, `nextAction=NONE`이라 그 단계로 이동하지 않는다.
이 P2 계획 schema가 별도 `contracts/changes/`로 승인되면 모델이
stale/invalid/uncalibrated이거나 HMM max posterior gate를 통과하지 못할 때 해당 nested
component를 `status=ABSTAIN`으로 표현하고, high entropy는 secondary diagnostic WARN으로
남긴다. fresh quote·owner position·margin·loss/stress 같은 hard guard가 별도로 실패한
경우에만 경제적 `HOLD/BLOCK`을 만든다.

현재 P0 Signal/RiskDecision success wire는 HMM state/probability를 필수로 요구하므로
component `ABSTAIN`을 schema-valid하게 직렬화할 수 없다. 이 상태에서는 state를 위조하지
않고 기존 service/schema failure 경로로 `HOLD`한다. 이 경계 HOLD를 “HMM posterior가 주문을
차단했다”라고 기록하지 않으며, P2 nested status 활성화는 schema·examples·API mapping을
함께 바꾸는 별도 계약 변경 이후에만 가능하다.

### 5.4 derivative_order_event

```json
{
  "schema": "derivative_order_event",
  "fixtureClass": "POST_ACTIVATION_WIRE_SHAPE_ONLY",
  "eventId": "doe_20260623_001",
  "orderId": "kis_deriv_mock_001",
  "orderIntentId": "doi_orderable_shape_001",
  "logicalActionId": "dla_orderable_shape_001",
  "actionGeneration": 1,
  "submissionAttemptId": "dsa_20260623_001",
  "riskReservationId": "drr_20260623_001",
  "riskReservationGeneration": 43,
  "riskLedgerVersionVector": [
    {"scope": "ACCOUNT_GLOBAL", "scopeId": "mock-derivatives-01", "generation": 42},
    {"scope": "PRODUCT_GROUP", "scopeId": "KOSPI200_OPTION", "generation": 17},
    {"scope": "STRATEGY", "scopeId": "HEDGE", "generation": 9}
  ],
  "safetyGateGeneration": 88,
  "attemptGeneration": 1,
  "physicalAttemptCount": 1,
  "sendStartedAt": "2026-06-23T10:15:24+09:00",
  "intentLifecycleState": "SUBMITTED",
  "sendDisposition": "ACKNOWLEDGED",
  "decisionId": "dec_orderable_shape_001",
  "requestId": "req_deriv_20260623_001",
  "idempotencyKeyHash": "0000000000000000000000000000000000000000000000000000000000000000",
  "canonicalRequestHash": "0000000000000000000000000000000000000000000000000000000000000000",
  "canonicalActionHash": "0000000000000000000000000000000000000000000000000000000000000000",
  "confirmationDisclosureHash": "0000000000000000000000000000000000000000000000000000000000000000",
  "idempotencyHashKeyVersion": "hmac-key-v1",
  "actionHashKeyVersion": "hmac-key-v1",
  "disclosureHashKeyVersion": "hmac-key-v1",
  "confirmationViewId": "dcv_orderable_shape_001",
  "submissionReceiptId": "dcr_submit_orderable_shape_001",
  "marginSnapshotId": "dms_orderable_shape_001",
  "valuationSnapshotId": "dvs_orderable_shape_001",
  "regimeEvidenceId": "hme_orderable_shape_001",
  "exposureSnapshotId": "des_orderable_shape_001",
  "stressSnapshotId": "dst_orderable_shape_001",
  "brokerageMode": "KIS_DERIVATIVE_MOCK",
  "kisTrId": "VTTO1101U",
  "status": "SUBMITTED",
  "symbol": "provider-verified-symbol-ref",
  "contracts": 1,
  "filledContracts": 0,
  "avgFillPrice": null,
  "fills": [],
  "positionsAfter": [],
  "providerReceipt": {
    "sourceType": "PROJECT_FIXTURE",
    "receiptId": "kis_mock_receipt_20260623_001",
    "payloadHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "rawStored": false
  },
  "occurredAt": "2026-06-23T10:15:25+09:00"
}
```

event와 audit trace는 hard guard인 `marginSnapshotId`, `exposureSnapshotId`,
`stressSnapshotId`를 항상 보존한다. `valuationSnapshotId`는 생성된 경우에만 보존하는
optional reference이고 HMM은 typed `regimeEvidenceId`로 연결한다. model 값 자체를 provider
주문 승인 근거로 승격하지 않으며 idempotency key 원문은 event에 포함하지 않는다.
`logicalActionId`, `actionGeneration`, `submissionAttemptId`, `attemptGeneration`,
`riskReservationId`, `riskReservationGeneration`, `riskLedgerVersionVector`,
`safetyGateGeneration`, `physicalAttemptCount`와
`sendStartedAt`은 active action fence, hierarchical risk capacity와 실제 transport 진입을
대사하는 server-issued audit reference다. 자동 reconciliation에 사용한
`providerOrderBaselineRef`, writer policy와 sanitized provider correlation이 있으면 같은
attempt audit에 보존한다.
event의 `idempotencyKeyHash`는 DB `idempotencyScopeHash`의 audit 직렬화명이며 같은 HMAC
digest다. raw key의 단순 SHA나 reversible 값이 아니다.

`status=SUBMITTED` event는 `intentLifecycleState=SUBMITTED`,
`sendDisposition=ACKNOWLEDGED`와 내부 `orderId`를 요구한다. 전송 후 결과가 불명확하면
`status=PENDING_RECONCILIATION`, `intentLifecycleState=PENDING_RECONCILIATION`,
`sendDisposition=AMBIGUOUS`이며 확인되지 않은 provider order ID나 receipt를 꾸며 넣지
않는다. 명확한 pre-send 실패 또는 provider reject는
`intentLifecycleState=SUBMISSION_FAILED`와 `sendDisposition=NOT_SENT` 또는
`DEFINITIVELY_REJECTED`로 기록한다. ambiguous attempt를 조회한 결과 provider 미접수가
권위 있게 확인된 경우에만 `CONFIRMED_NOT_ACCEPTED`를 사용한다.

receipt provenance는 주문 종류에 따라 다음 `oneOf`를 적용한다.

| 주문 종류 | event 필수 receipt | event 필수 view/digest provenance | 금지 필드 |
|---|---|---|---|
| post-trade naked short call 노출 생성·증가가 아닌 일반 submission(선물, 옵션 매수, long sell-to-close, 지원 중인 naked short call buy-to-close) | `submissionReceiptId` 1개 | singular `confirmationViewId`, `confirmationDisclosureHash`, `disclosureHashKeyVersion` | `confirmationReceiptIds`, plural view/hash/key-version fields |
| post-trade naked short call 노출 생성·증가 | step 순서의 `confirmationReceiptIds` 정확히 2개 | step 순서의 `confirmationViewIds`, `confirmationDisclosureHashes`, `disclosureHashKeyVersions` 각각 정확히 2개 | `submissionReceiptId`, singular view/hash/key-version fields |

post-trade naked short call 노출 생성·증가 event의 receipt·view·digest·key-version 배열은
소비된 step1→step2 chain과 exact match해야 한다. receipt 원문, confirmation 문구, actor
입력값은 event에 복사하지 않고 server-side audit record로 연결한다.

### 5.5 P2 내부 async event 원칙

P2 derivative event는 공용 API 계약이 아니다. P2 기능 flag가 OFF이면 내부 async event도 발행하지 않는다. 이벤트 형식, 재처리, 중복 처리, payload 제한은 공개 API 계약 밖의 Decision Platform 내부 구현 기록에서 관리한다.

`providerEvidence`와 `providerReceipt`는 allowlist로 정규화한 수치·opaque receipt id·sanitized payload hash만 가진다. provider raw body/header/request URL, provider 계좌번호, token/key는 저장하거나 참조하지 않는다.

---

## 6. REST API

### 6.1 Derivative Universe 조회

`GET /api/v1/derivatives/universe?productGroup=KOSPI200_OPTION&orderableOnly=false&limit=100&cursor=opaque`

응답:

```json
{
  "success": true,
  "data": {
    "asOf": "2026-06-23T10:00:00+09:00",
    "items": [
      {
        "assetType": "DOMESTIC_OPTION",
        "productGroup": "KOSPI200_OPTION",
        "symbol": "201V6300",
        "underlyingSymbol": "KOSPI200",
        "maturity": "2026-09",
        "lastTradingDate": "2026-09-10",
        "lastTradingAt": "2026-09-10T15:20:00+09:00",
        "finalSettlementDate": "2026-09-11",
        "exerciseStyle": "EUROPEAN",
        "settlementType": "CASH",
        "contractMultiplier": 250000,
        "strikePrice": 310.0,
        "optionRight": "PUT",
        "fixtureClass": "SYNTHETIC_NOT_ORDERABLE",
        "providerSymbolVerified": false,
        "contractMasterVersion": "PROJECT_FIXTURE_V1",
        "contractMasterSourceHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "contractMasterAsOf": "2026-06-23T10:00:00+09:00",
        "tickRuleId": "PROJECT_FIXTURE_TICK",
        "tickRuleVersion": "1",
        "discovered": true,
        "allowlisted": false,
        "orderable": false,
        "activationStatus": "SYNTHETIC_NOT_ORDERABLE",
        "killSwitchActive": false,
        "liquidityStatus": "OK"
      }
    ],
    "nextCursor": null
  }
}
```

`orderableOnly=true`는 `orderable=true` item만 반환한다. `limit` 기본/최대는 100이고
stable order는 `(productGroup, symbol)` 오름차순이다. cursor는
필터·master version에 결속된 opaque 값이며 unknown/expired cursor는 `INVALID_REQUEST`다.
KIS 공식 GitHub `stocks_info`의 국내 지수·상품·주식 선물옵션 master file은
`UPSTREAM_ONLY→ADOPTED_WITH_OVERRIDE` 후보지만, sample parser의 TLS 검증 우회 코드는
`REJECTED_UNSAFE_DEFAULT`다. project collector는 fixed HTTPS origin, TLS 검증,
redirect/압축·크기·entry/schema/CP949 decode/hash guard를 별도로 구현해야 한다. KIS
master의 symbol/type/strike/month 정보는 KRX 공식 상품별 exercise/settlement/multiplier/
last-trading-session/tick rule과 대조한다. exact source·update cadence·freshness·fixture가
고정되기 전 item은 discovery에는 보일 수 있어도 `orderable=false`다. v1 주문 allowlist는
검증된 KOSPI200 future/option product group으로만 좁히며 다른 국내 상품군은 개별 source
card 승인 전 비활성이다. price가 tick/band에 맞지 않으면 server가 임의 반올림하지 않고
거부하며 canonical hash와 provider payload는 같은 validated decimal을 사용한다.

### 6.2 Margin Snapshot 생성

`POST /api/v1/derivatives/margin-snapshots`

요청:

```json
{
  "accountId": "mock-derivatives-01",
  "orderIntentId": "doi_20260623_001"
}
```

server는 owner-scoped canonical DRAFT에서 asset/symbol/side/contracts/order type/price를
읽는다. client가 중복 business field를 보내면 unknown-field rejection으로 거부한다.
`DAY` session은 client 입력이 아니라 검증된 KRX 거래일·상품별 정규/최종거래일 시간과
server clock에서 파생한다. 휴장·조기/최종 종료와 `now>=lastTradingAt`이면 margin snapshot과
provider 호출을 만들지 않는다.

응답은 `derivative_margin_snapshot` schema를 따른다.

### 6.3 Order Candidate 생성

`POST /api/v1/derivatives/order-candidates`

요청:

```json
{
  "portfolioId": "pf_p1_001",
  "strategyRole": "HEDGE",
  "candidateMode": "GUIDE",
  "maxCandidates": 3,
  "basis": {
    "signalId": "sig_001",
    "modelEvaluationId": "mev_001",
    "regimeEvidenceId": "hme_20260623_001",
    "portfolioRiskSnapshotId": "prs_20260623_001"
  }
}
```

응답:

```json
{
  "success": true,
  "data": {
    "candidates": [
      {
        "lifecycleState": "DRAFT",
        "orderIntentId": "doi_20260623_001",
        "assetType": "DOMESTIC_OPTION",
        "productGroup": "KOSPI200_OPTION",
        "symbol": "201V6300",
        "strategyRole": "HEDGE",
        "side": "BUY",
        "positionEffect": "OPEN",
        "contracts": 1,
        "orderType": "LIMIT",
        "limitPrice": 2.40,
        "orderable": false,
        "activationStatus": "SYNTHETIC_NOT_ORDERABLE",
        "reason": "P1 포트폴리오 beta·deterministic exposure를 줄이는 보호적 put 후보. HMM RISK_OFF는 WARN context"
      }
    ]
  }
}
```

`candidates[]`는 `derivative_order_candidate_summary` view이며 5.1의 canonical
`derivative_order_intent` schema가 아니다. production server는 candidate를 반환하기 전에
official contract master 전체를 검증해 canonical DRAFT를 저장하고, summary에는 화면에
필요한 allowlisted 필드만 싣는다. 위 synthetic fixture는 offline schema/math 검증용 DRAFT라
`orderable=false`이며 confirm/submit으로 전진하지 않는다. 이후 평가·snapshot API는
`orderIntentId`로 owner-scoped canonical DRAFT를 다시 읽으므로 summary의 생략 필드나
client 수정값을 계약 입력으로 사용하지 않는다.

client는 HMM state, volatility label, portfolio beta 같은 파생 scalar를 보내지 않는다.
server는 owner-scoped `regimeEvidenceId`, `modelEvaluationId`, `portfolioRiskSnapshotId`에서
source hash·freshness·policy version을 검증해 재구성한다. native HMM state는 active S6.1
producer가 내보내는 `RISK_ON|RISK_OFF` 중 하나이며
`P(z_t|x_1..x_t)` causal posterior를 사용한다. `NORMAL`, `SIDEWAYS`,
`HIGH_VOLATILITY`는 P0 wire 호환 enum에 남아 있어도 active 2-state HMM의 native state가
아니다. deterministic volatility regime은 realized-volatility threshold로 계산한 별도
rule label이고 두 값을 합쳐 하나의 HMM state처럼 저장하지 않는다.

intent의 take-profit/stop-loss/time-limit은 v1 `GUIDE_ONLY` display/journal metadata다.
`CONDITION_LIMIT`은 disabled이고 provider trigger나 자동 청산을 만들지 않으며 gap/slippage
때문에 표시 가격의 체결을 보장하지 않는다.

### 6.4 Decision API 확장

`POST /api/v1/decisions/evaluate-order`

P1 endpoint를 그대로 사용하되, 요청 body에는 canonical object 복사본이 아니라
`orderIntentType=DERIVATIVE`와 `derivativeOrderIntentRef`를 포함한다.

```json
{
  "orderIntentType": "DERIVATIVE",
  "derivativeOrderIntentRef": {
    "orderIntentId": "doi_20260623_001",
    "marginSnapshotId": "dms_20260623_001"
  },
  "principleId": "prc_001",
  "mode": "GUIDE"
}
```

server는 ref로 canonical DRAFT와 owner-bound margin snapshot을 읽고 action hash·freshness를
검증한다. margin이 없거나 stale이면 이 endpoint가 암묵적으로 provider를 다시 호출하지 않고
`MARGIN_GUARD_FAILED` 또는 `DATA_STALE`로 종료한다. 검증된 contract master·owner position·
quote로 exposure/stress와 선택적인 valuation을 계산한 뒤 risk decision을 만든다. server는
decision과 hard snapshot reference를 저장하고 canonical intent의
`DRAFT → EVALUATED`를 같은 DB transaction의 CAS로 전이한다. schema/owner/action 검증이나
CAS가 실패하면 EVALUATED 상태를 남기지 않는다. `HOLD|BLOCK` decision도 감사 가능한 평가
결과로 저장할 수 있지만 6.4.1/6.4.2 confirmation은 `ALLOW|WARN` 중 정책상 제출 가능한
decision만 받는다. 응답은 `derivative_risk_decision`을
`data.derivativeRiskDecision`에 포함한다.
margin/exposure/stress snapshot과 decision은 평가 시점의 동일한
`riskLedgerVersionVector`에 결속한다. 이 vector는 owner/account global, product group,
strategy scope의 position·open/pending order와 기존 risk reservation을 포함한다.

### 6.4.0 확인 화면 challenge 발급 (P2 계획 계약)

`POST /api/v1/derivatives/order-intents/{orderIntentId}/confirmation-view`

Bearer 인증과 `X-Idempotency-Key`가 필요하다. server는 current decision/hard snapshot,
position/risk-ledger, template/version/locale를 다시 읽고 5.1의 exact disclosure object를
렌더링한다. 일반 주문에는 `GENERAL_SUBMISSION/1`, post-trade naked short call 노출
생성·증가에는 `OPTION_SHORT/1`만 이 endpoint에서 발급한다. `OPTION_SHORT/2`는 유효한
step1 확인 성공 transaction에서만 발급한다. 응답은 hash 대상인 canonical disclosure
전체와 server-issued challenge metadata를 함께 반환하며, 화면은 이 `disclosure`를
그대로 렌더링해야 한다.

> 아래 JSON은 provider-verified master와 submit-eligible `ALLOW|WARN` decision이
> 활성화된 뒤의 `POST_ACTIVATION_WIRE_SHAPE_ONLY` fixture다. 앞 절의
> `SYNTHETIC_NOT_ORDERABLE` HOLD trace와 연결되지 않으며 현재 accepted response 또는
> provider 호출을 뜻하지 않는다.

```json
{
  "confirmationViewId": "dcv_orderable_shape_001",
  "confirmationDisclosureHash": "0000000000000000000000000000000000000000000000000000000000000000",
  "disclosureHashKeyVersion": "hmac-key-v1",
  "confirmationPurpose": "GENERAL_SUBMISSION",
  "confirmationStep": 1,
  "disclosureTemplateId": "derivative-order-confirmation",
  "disclosureTemplateVersion": "1",
  "disclosureTemplateContentHash": "6666666666666666666666666666666666666666666666666666666666666666",
  "locale": "ko-KR",
  "disclosure": {
    "schemaVersion": "derivative-confirmation-disclosure-v1",
    "hashKeyVersion": "hmac-key-v1",
    "confirmationPurpose": "GENERAL_SUBMISSION",
    "confirmationStep": 1,
    "ownerUserId": "usr_demo_001",
    "accountId": "mock-derivatives-01",
    "orderIntentId": "doi_orderable_shape_001",
    "logicalActionId": "dla_orderable_shape_001",
    "actionGeneration": 1,
    "canonicalActionHash": "0000000000000000000000000000000000000000000000000000000000000000",
    "decisionId": "dec_orderable_shape_001",
    "principleId": "prc_orderable_shape_001",
    "principleVersion": 1,
    "decisionValidUntil": "2026-06-23T10:17:20+09:00",
    "riskLedgerVersionVector": [
      {"scope": "ACCOUNT_GLOBAL", "scopeId": "mock-derivatives-01", "generation": 42},
      {"scope": "PRODUCT_GROUP", "scopeId": "KOSPI200_OPTION", "generation": 17},
      {"scope": "STRATEGY", "scopeId": "HEDGE", "generation": 9}
    ],
    "positionStructureHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "positionEffect": "OPEN",
    "riskReductionOnly": false,
    "signedPositionContractsBefore": 0,
    "signedPositionContractsAfter": 1,
    "coverageType": "NOT_APPLICABLE",
    "lossProfile": "BOUNDED",
    "maxLossAmount": 615000,
    "maxLossCurrency": "KRW",
    "contracts": 1,
    "contractMultiplier": 250000,
    "premiumCashflow": -600000,
    "estimatedFees": 15000,
    "currency": "KRW",
    "exposureUnit": "KRW_NOTIONAL",
    "marketValueApplicability": "APPLICABLE",
    "disclosureTemplateId": "derivative-order-confirmation",
    "disclosureTemplateVersion": "1",
    "disclosureTemplateContentHash": "6666666666666666666666666666666666666666666666666666666666666666",
    "locale": "ko-KR",
    "marginSnapshot": {
      "id": "dms_orderable_shape_001",
      "asOf": "2026-06-23T10:15:22+09:00",
      "expiresAt": "2026-06-23T10:16:22+09:00",
      "availableMarginCollateral": 7200000,
      "requiredMarginEstimate": 600000,
      "marginBufferRate": 0.2,
      "marginBufferAmount": 120000,
      "providerEvidenceHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "normalizationFormulaVersion": "margin-collateral-v1"
    },
    "exposureSnapshot": {
      "id": "des_orderable_shape_001",
      "asOf": "2026-06-23T10:15:22+09:00",
      "expiresAt": "2026-06-23T10:16:22+09:00",
      "positionStructureHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "sourceHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "grossContractNotional": 88812500,
      "conservativeDeltaEquivalentExposure": -88812500,
      "conservativeRiskDelta": -1.0,
      "riskDeltaMethod": "CONSERVATIVE_CONTRACT_BOUND_V1",
      "riskDeltaContractSourceHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "grossCurrentMarketValue": 600000,
      "marketValueCurrency": "KRW",
      "marketValueAsOf": "2026-06-23T10:15:20+09:00"
    },
    "stressSnapshot": {
      "id": "dst_orderable_shape_001",
      "asOf": "2026-06-23T10:15:22+09:00",
      "expiresAt": "2026-06-23T10:16:22+09:00",
      "sourceHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "orderedScenarios": [
        {
          "scenarioId": "OPTION_EXPIRES_WORTHLESS",
          "outputKind": "DETERMINISTIC_STRESS",
          "probability": null,
          "result": {"kind": "LOSS_AMOUNT", "amount": 615000, "currency": "KRW"},
          "valuationMethod": "PREMIUM_PLUS_ESTIMATED_FEES_V1",
          "formulaVersion": "long-option-loss-bound-v1",
          "inputHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
          "sourceHash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        }
      ]
    },
    "sortedResidualRiskCodes": [
      "DISCRETE_HEDGE_RISK",
      "GAP_RISK",
      "LIQUIDITY_RISK",
      "MODEL_RISK"
    ]
  },
  "issuedAt": "2026-06-23T10:15:22+09:00",
  "expiresAt": "2026-06-23T10:16:22+09:00"
}
```

`confirmationViewId`는 owner/account/intent/logical action/generation/decision/snapshot/
exact disclosure hash에 결속되고 TTL은 모든 hard evidence의 최소 만료보다 길 수 없다.
확인 POST는 이 view ID와 화면에서 받은 expected hash를 echo해야 한다. server는 저장한
canonical bytes와 current disclosure를 모두 대조하고 성공 transaction에서 view를
`ISSUED → CONSUMED`로 한 번만 전이한다. current canonical disclosure가 달라졌거나 view가
만료·소비·cross-owner이면 receipt 없이 새 view를 요구하고 provider 호출은 0건이다. 이렇게
confirmation-view POST와 confirmation POST 사이 position·margin·stress 변경으로 사용자가
보지 않은 설명에 receipt가 발급되는 TOCTOU를 차단한다.

### 6.4.1 일반 주문 제출 준비 receipt (P2 계획 계약)

`POST /api/v1/derivatives/order-intents/{orderIntentId}/confirm`

Bearer 인증과 `X-Idempotency-Key`가 필요하다. 이 endpoint는 선물과 post-trade short
노출이 생성·증가하지 않는 옵션 거래(매수, long sell-to-close, short buy-to-close)에
사용한다. naked short call 노출 생성·증가는 6.4.2의 2단계 확인만 허용한다.

```json
{
  "decisionId": "dec_orderable_shape_001",
  "marginSnapshotId": "dms_orderable_shape_001",
  "exposureSnapshotId": "des_orderable_shape_001",
  "stressSnapshotId": "dst_orderable_shape_001",
  "confirmationViewId": "dcv_orderable_shape_001",
  "expectedConfirmationDisclosureHash": "0000000000000000000000000000000000000000000000000000000000000000"
}
```

server는 subject/account/order의 `canonicalActionHash`, 미만료·submit-eligible
`ALLOW|WARN` decision, hard snapshot을 다시 검증한다. 발급되는 receipt는
`logicalActionId`, `actionGeneration`, `canonicalActionHash`,
`confirmationDisclosureHash`, `issuedAt`과 hard snapshot reference에 결속한다.
confirmation 진입 시 decision 또는 hard snapshot이 이미 만료됐다면 receipt를 만들지 않고
`EVALUATED → EXPIRED` CAS와 기존 confirmation receipt 무효화를 같은 transaction에서
수행한다. provider 호출은 0건이다.
검증과 사용자 확인이 성공하면 같은 DB transaction에서 one-time
`submissionReceiptId`를 저장하고 canonical intent를 `EVALUATED → SUBMIT_READY`로 CAS
전이하며 `confirmationViewId`를 `CONSUMED`로 만든다. 이 단계에서는 provider 호출을
만들지 않는다.

```json
{
  "submissionReceiptId": "dcr_submit_orderable_shape_001",
  "orderIntentId": "doi_orderable_shape_001",
  "lifecycleState": "SUBMIT_READY",
  "issuedAt": "2026-06-23T10:15:23+09:00",
  "expiresAt": "2026-06-23T10:16:22+09:00"
}
```

receipt TTL은 decision과 모든 hard snapshot의 남은 유효시간을 넘지 않는다. 하나라도
만료되면 intent가 저장상 `SUBMIT_READY`여도 제출 자격은 즉시 무효다. 제출 또는 expiry
reaper가 동일 intent를 선점해 `SUBMIT_READY → EXPIRED`로 CAS하고 receipt를 무효화한다.
provider 호출 전 만료를 발견한 제출 요청도 같은 transaction을 수행하고 provider 호출은
0건이다. 재평가가 필요하면 `supersedesOrderIntentId`로 연결한 새 DRAFT와 새 margin
snapshot부터 시작한다. receipt 생성 또는 CAS가 실패하면 둘 다 rollback한다.

### 6.4.2 post-trade naked short call 노출 생성·증가 2단계 확인 receipt 발급 (P2 계획 계약)

이 절은 P2 feature가 기본 OFF인 현재 호출 가능한 API가 아니라, post-trade naked short
call 노출 생성·증가 기능을 구현할 때 함께 승인해야 하는 계획 계약이다. 두 endpoint 모두
Bearer 인증과 금융 safety write용 `X-Idempotency-Key`를 요구한다.

> 아래 step1/step2 JSON은 mandatory hard stress set과 provider mapping이 모두 승인된
> `POST_ACTIVATION_WIRE_SHAPE_ONLY` ID를 사용한다. 5.2.3의 부분 stress 수치 예시는 독립
> 계산 fixture이며 submit-eligible confirmation에 사용할 수 없다.

1차 확인:

`POST /api/v1/derivatives/confirmations/option-short/step1`

```json
{
  "orderIntentId": "doi_short_orderable_shape_001",
  "decisionId": "dec_short_orderable_shape_001",
  "marginSnapshotId": "dms_short_orderable_shape_001",
  "exposureSnapshotId": "des_short_orderable_shape_001",
  "stressSnapshotId": "dst_short_orderable_shape_001",
  "confirmationViewId": "dcv_short_step1_orderable_shape_001",
  "expectedConfirmationDisclosureHash": "1111111111111111111111111111111111111111111111111111111111111111"
}
```

서버는 JWT subject와 owner-scoped order intent에서 opaque accountId를 구하고,
미만료·submit-eligible `ALLOW|WARN` decision을 확인한 뒤 server-reconstructed post-trade
shape가 exact v1 `NAKED_SHORT_CALL`, `coverageType=NAKED`,
`lossProfile=UNBOUNDED`, `maxLossAmount/maxLossCurrency=null`인지 확인한다. short put,
covered/spread/multi-leg는 step1 전에 `CANDIDATE_DISABLED`다. 주문 방향·상품·계약수·
가격·exposure·probability-free stress·margin·잔여위험 설명의
`disclosureTemplateId=option-short-confirmation`,
`disclosureTemplateVersion`, immutable `disclosureTemplateContentHash`, `locale`까지
포함해 `confirmationDisclosureHash`를 계산한다. 화면 문구
template/version/content hash/locale가 바뀌면 새 확인이 필요하다. provider-bound 불변 주문 필드의
`canonicalActionHash`는 별도로 계산하며 두 digest를 서로 대체하지 않는다. 성공
transaction은 step1 view를 `CONSUMED`로 만들고 opaque step1 `receiptId`를 발급하는 동시에
`confirmationPurpose=OPTION_SHORT`, `confirmationStep=2`, 별도
`confirmationViewId`·별도 disclosure hash·step2 prompt/template를 가진
`nextConfirmationView`를 발급한다. `nextConfirmationView`는 6.4.0과 같은 응답 shape이며
hash 대상인 canonical `disclosure` 전체를 생략 없이 포함한다. step1 view나 hash를 step2에
재사용하는 응답은 schema 오류다.

2차 확인:

`POST /api/v1/derivatives/confirmations/option-short/step2`

```json
{
  "step1ReceiptId": "dcr_step1_short_orderable_shape_001",
  "orderIntentId": "doi_short_orderable_shape_001",
  "decisionId": "dec_short_orderable_shape_001",
  "marginSnapshotId": "dms_short_orderable_shape_001",
  "exposureSnapshotId": "des_short_orderable_shape_001",
  "stressSnapshotId": "dst_short_orderable_shape_001",
  "confirmationViewId": "dcv_short_step2_orderable_shape_001",
  "expectedConfirmationDisclosureHash": "2222222222222222222222222222222222222222222222222222222222222222"
}
```

서버는 유효한 1차 receipt와 동일한
subject/account/orderIntent/logicalAction/actionGeneration/decision/marginSnapshot/
exposureSnapshot/stressSnapshot/canonicalActionHash인지 검증하고, step1과 다른
`confirmationViewId`, `confirmationDisclosureHash`, `confirmationStep=2`인지 확인한 뒤
별도 nonce의 `step=2` receipt를 발급한다. 이 transaction은 step2 view와 step1 receipt를
각각 `CONSUMED`로 만들며 둘 중 하나라도 이미 소비됐다면 전부 rollback한다. 기본 TTL은 각
단계 2분이며 decision `validUntil`과 각 snapshot `expiresAt`보다 길 수 없다. 한 1차
receipt는 2차 receipt 하나에만 소비되고, 주문 제출은 최종 step2 receipt를 같은
트랜잭션에서 `CONSUMED` 처리하며 이미 소비된 step1 receipt와 두 view의 chain을 검증한다.
만료·재사용·역순·다른 subject/account/order/action에 대한 receipt나 view는
`RISK_BLOCKED`로 거부하고 provider 호출을 만들지 않는다. actor와 issued/consumed 시각은
principal과 서버 clock에서 생성한다. 2차 receipt 저장과 canonical intent의
`EVALUATED → SUBMIT_READY` CAS는 같은 DB transaction이다. 둘 중 하나가 실패하면 모두
rollback하며 provider 호출은 0건이다. 성공 응답은 opaque `receiptId`, `step=2`,
`lifecycleState=SUBMIT_READY`, `issuedAt`, `expiresAt`만 반환한다.
step2 전 step1 receipt, decision 또는 hard evidence가 만료되면
`EVALUATED → EXPIRED` CAS와 발급된 receipt 무효화를 원자 수행한다. step2로
`SUBMIT_READY`가 된 뒤 최종 step2 receipt 또는 hard evidence가 만료되면
`SUBMIT_READY → EXPIRED`를 적용한다. 두 경로 모두 provider 호출은 0건이고 재시도는 새
DRAFT version에서 1차 확인부터 다시 시작한다.

### 6.4.3 만료 intent의 새 DRAFT version 생성 (P2 계획 계약)

`POST /api/v1/derivatives/order-intents/{expiredOrderIntentId}/renew`

Bearer 인증과 `X-Idempotency-Key`가 필요하다. server는 기존 intent가 요청 subject 소유이고
terminal `EXPIRED`이며 위 allowlist reason의 terminalization audit row가 있는지 확인한다.
같은 action을 다시 평가하는
경우에만 immutable contract/order 필드를 복사하고 새 `orderIntentId`,
같은 `logicalActionId`, 증가한 `actionGeneration`, `lifecycleState=DRAFT`,
`supersedesOrderIntentId=expiredOrderIntentId`를 원자 저장한다.
이 endpoint는 KIS/provider 호출을 만들지 않는다.

```json
{
  "orderIntentId": "doi_20260623_002",
  "logicalActionId": "dla_20260623_001",
  "actionGeneration": 2,
  "lifecycleState": "DRAFT",
  "supersedesOrderIntentId": "doi_20260623_001",
  "renewalReason": "EVIDENCE_EXPIRED"
}
```

이전 `decisionId`, margin/exposure/stress/valuation snapshot, receipt, model evidence와
`asOf/expiresAt`은 복사하지 않는다. 새 DRAFT는 6.2의 margin snapshot부터 다시 시작한다.
상품·side·position effect·계약수·가격 등 action을 바꾸려면 renew가 아니라 6.3에서 새
candidate를 생성한다. old intent가 EXPIRED가 아니거나 owner/action 검증이 실패하면
`NOT_FOUND` 또는 `RISK_BLOCKED`이고 새 DRAFT와 provider 호출은 0건이다. 같은 idempotency
scope의 재호출은 최초 생성된 새 DRAFT를 반환한다.

### 6.5 Mock 주문 제출

`POST /api/v1/derivatives/mock/orders`

필수 header:

| Header | 설명 |
|---|---|
| `X-Idempotency-Key` | 중복 주문 방지 (P1 공통 규칙과 동일) |

`decisionId`와 `marginSnapshotId`는 header가 아니라 요청 body로만 전달한다. 같은 값을 header와 body에 중복시키면 불일치 시 처리 규칙이 따로 필요해지므로 body로 일원화한다.

제출 시 server는 `orderIntentId`로 canonical intent를 다시 읽고
`lifecycleState=SUBMIT_READY`, 미만료·submit-eligible `ALLOW|WARN`
decision/margin/exposure/stress, owner/action hash를
검증한다. `valuationSnapshotId`는 있으면 event/audit에 결속하지만 누락·ABSTAIN만으로 제출을
막지 않는다. client가 보낸 lifecycle 문자열은 상태 전이 증거로 인정하지 않는다.
아래 두 body는 앞 절의 HOLD synthetic trace와 연결되지 않는
`POST_ACTIVATION_WIRE_SHAPE_ONLY` 예시다.

일반 submission(선물, 옵션 매수, long sell-to-close, 지원 중인 naked short call
buy-to-close) 요청 예시:

```json
{
  "orderIntentId": "doi_orderable_shape_001",
  "decisionId": "dec_orderable_shape_001",
  "marginSnapshotId": "dms_orderable_shape_001",
  "exposureSnapshotId": "des_orderable_shape_001",
  "stressSnapshotId": "dst_orderable_shape_001",
  "submissionReceiptId": "dcr_submit_orderable_shape_001"
}
```

post-trade naked short call 노출 생성·증가 요청 예시:

```json
{
  "orderIntentId": "doi_short_orderable_shape_001",
  "decisionId": "dec_short_orderable_shape_001",
  "marginSnapshotId": "dms_short_orderable_shape_001",
  "exposureSnapshotId": "des_short_orderable_shape_001",
  "stressSnapshotId": "dst_short_orderable_shape_001",
  "confirmationReceiptIds": [
    "dcr_step1_short_orderable_shape_001",
    "dcr_step2_short_orderable_shape_001"
  ]
}
```

응답은 `derivative_order_event` schema를 따른다.

post-trade naked short call 노출 생성·증가는 ordered receipt ID 두 개가 모두 필요하다.
step1 receipt는 step2 발급 transaction에서 이미 한 번 소비되고
`consumedByReceiptId=step2ReceiptId`로 결속돼야 한다. 주문 제출 시에는 이 immutable
step1 lineage와 아직 미소비인 최종 step2 receipt의
subject/account/orderIntent/logicalAction/action-generation/decision/marginSnapshot/
exposureSnapshot/stressSnapshot/action-hash/issuedAt/TTL/단계 순서를 다시 검증하고
step2 receipt만 원자적으로 소비한다. 다른 주문의 receipt, 잘못된 step1 소비 대상, 만료 receipt,
재사용 receipt, boolean 확인값은 인정하지 않는다. 일반 submission은 6.4.1의
`submissionReceiptId` 한 개를 같은 binding·TTL·one-time 규칙으로 소비한다. provider
호출을 시작하기 전에 receipt와 idempotency record의 원자적 선점을 완료한다.

provider outbound 권한은 다음 순서로 고정한다.

1. P1 Redis idempotency owner claim을 먼저 획득하되 실패하면 DB claim과 provider 호출은 0건이다.
   이 Redis record는 replay 조정용이고 금융 부작용의 최종 authority가 아니다.
2. 같은 DB transaction에서 canonical intent를 `SUBMIT_READY → SUBMISSION_CLAIMED`로 CAS하고,
   receipt, decision one-use, HMAC 기반
   `UNIQUE(subject, method, routeTemplate, idempotencyScopeHash)`, unique
   orderIntent/decision/receipt reference, `(owner, account, canonicalActionHash)` active
   submission fence를 선점한다. 같은 transaction에서 최신 position+open/pending order와
   기존 reservation을 포함한 hierarchical risk ledger를
   `ACCOUNT_GLOBAL→PRODUCT_GROUP→STRATEGY` deterministic 순서로 잠그고
   `riskLedgerVersionVector`를 CAS해 contracts·margin·exposure·stress
   capacity를 모든 scope에서 `riskReservationId/riskReservationGeneration`으로 예약한다.
   `providerWriterPolicy=EXCLUSIVE_APP_WRITER`, `submissionAttemptId`,
   `attemptGeneration`, 현재 `safetyGateGeneration`,
   `physicalAttemptCount=0`, `sendDisposition=NOT_STARTED`도 원자 저장한다.
   `canonicalRequestHash`는 unique tuple에 넣지 않고 같은 idempotency scope의 기존 값과
   비교한다. 같은 key+같은 payload는 기존 결과를 replay하고 같은 key+다른 payload는
   `IDEMPOTENCY_CONFLICT`로 rollback한다. 이때 receipt, decision과 모든 hard snapshot의
   최소 만료시각을 `sendNotAfter`로 저장한다.
   snapshot/decision의 `riskLedgerVersionVector`가 current와 다르거나 어느 scope에서든
   합산 capacity를 예약할
   수 없으면 claim·receipt 소비·attempt/reservation은 0건이다. 대신 같은 locked
   risk-ledger transaction에서 `SUBMIT_READY→EXPIRED`와 receipt 무효화만 commit해 새
   평가를 요구하고 provider 호출은 0건이다.

`canonicalRequestHash`는 route별 `schemaVersion`을 포함한 purpose-separated HMAC이다.
unknown field를 먼저 거부하고, UTF-8 object key는 정렬하며 모든 string은 Unicode NFC로
정규화한다. omitted와 explicit `null`은 서로 다르다. 금융 decimal은 binary float가 아니라
exact decimal로 parse해 range/tick을 검증한 뒤 exponent·불필요한 trailing zero 없는
canonical string으로 만들고 `-0`은 `0`으로 정규화한다. bool은 숫자로 허용하지 않는다.
array는 schema가 sorted set이라고 지정한 경우에만 정렬하며 그 밖에는 순서를 보존한다.
따라서 `2.4`와 `2.40`은 같은 LIMIT payload지만 omitted/null/0 MARKET 표현과 한 field 차이는
다른 payload다. hash key version은 row에 보존하고 active replay row가 남은 key는 retire하지
않는다.
3. 그 밖의 transaction/CAS/unique 제약 중 하나라도 실패하면 전부 rollback하고 provider
   호출은 0건이다. domain drift의 EXPIRED+receipt 무효화 경로를 정상 claim 실패와 섞지
   않는다.
4. commit 뒤 중앙 quota coordinator를 거친다. private transport의 첫 byte 직전에
   DB가 `lifecycleState=SUBMISSION_CLAIMED`, active action fence,
   attempt owner/generation, active risk reservation owner/generation/status와 현재
   `riskReductionOnly` invariant,
   `now<=sendNotAfter`, `physicalAttemptCount=0`을 다시 확인한다. 같은 transaction에서
   safety-gate row를 잠그고 Kill Switch OFF, product allowlisted, owner/account active,
   operator account allowlisted, `EXCLUSIVE_APP_WRITER`, active
   principle/policy exact version, required hard-data ID·generation·`asOf/expiresAt` freshness,
   current `safetyGateGeneration` exact match와 validated KRX calendar·상품별 session에서
   server-derived `DAY`, `now<lastTradingAt`을 재검증한
   뒤 `physicalAttemptCount=1`,
   `sendStartedAt=serverNow`로 CAS한다. deadline 또는 safety gate/session을 통과하지 못하면
   provider 호출 0건으로 `SUBMISSION_FAILED/NOT_SENT` 처리하고 risk reservation을 원자
   release한다. generation/lease가 맞지 않는 stale worker도 first byte 전에 거부한다.
   Kill Switch/allowlist/account 변경은 같은 safety-gate row generation update와 직렬화해
   변경 commit 뒤 count 0 attempt가 통과할 수 없다. CAS 뒤 worker가
   죽으면 실제 wire 전송 여부를 증명할 수 없으므로 재전송하지 않고
   `PENDING_RECONCILIATION`로 수렴한다.
5. private transport는 4번 CAS를 성공한 attempt만 provider send 최대 1회 수행한다.
   transport 자동 재시도는 금지한다.
6. 같은 idempotency key+payload 재요청은 Redis 유실 여부와 무관하게 DB attempt/order
   결과를 기준으로 동일 응답을 복원한다. 다른 key라도 같은 `orderIntentId`·`decisionId`는
   이미 `SUBMISSION_CLAIMED` 이후이고 DB unique/state 제약이 있으므로 두 번째 outbound가
   0건이어야 한다.
7. 새 intent·decision·receipt·key라도 active
   `(owner, account, canonicalActionHash)` fence가 있으면 outbound는 0건이다. renew와 같은
   사용자 action의 EXPIRED renew version만 `logicalActionId`를 유지하고
   `actionGeneration`을 증가시킨다. provider attempt가 terminal로 해소된 뒤 같은 canonical
   action을 의도적으로 다시 주문하려면 새 candidate·평가·사용자 확인으로 새
   `logicalActionId`와 `actionGeneration=1`을 발급받아야 한다. 새 확인
   receipt의 `issuedAt`은 이전 generation의 `terminalAt`보다 뒤여야 하므로, fence가 풀리기
   전에 미리 만든 candidate·receipt로 다음 주문을 시작할 수 없다.
8. `PENDING_RECONCILIATION`은 risk reservation을 유지한다. ACK는 reservation을 open-order
   allocation으로 전환하고, fill은 체결 수량을 position capacity로 옮기며 미체결 수량은
   계속 예약한다. `NOT_SENT`, `DEFINITIVELY_REJECTED`,
   `CONFIRMED_NOT_ACCEPTED`와 cancel의 미체결 수량만 같은 risk-ledger transaction에서
   release한다.

| send 결과 | intent 전이 | 후속 동작 |
|---|---|---|
| provider send 전 실패가 증명됨(`physicalAttemptCount=0`) | `SUBMISSION_CLAIMED → SUBMISSION_FAILED`, `sendDisposition=NOT_SENT` | receipt/decision은 재사용하지 않고 risk reservation release 후 새 candidate·평가 |
| provider의 명확한 reject | `SUBMISSION_CLAIMED → SUBMISSION_FAILED`, `sendDisposition=DEFINITIVELY_REJECTED` | risk reservation release와 typed failure audit 후 새 candidate·평가 |
| sanitized order acknowledgement 수신 | `SUBMISSION_CLAIMED → SUBMITTED`, `sendDisposition=ACKNOWLEDGED` | risk reservation을 open-order allocation으로 전환하고 내부 order와 `SUBMITTED` event 생성 |
| timeout·connection loss·응답 parse 실패 등 send 결과 불명 | `SUBMISSION_CLAIMED → PENDING_RECONCILIATION`, `sendDisposition=AMBIGUOUS` | reservation·action fence 유지, 자동 재전송·renew 금지, order/fill 조회 |

worker가 `physicalAttemptCount=1`을 기록한 뒤 결과 전이 전에 죽거나 lease가 만료돼도
재전송하지 않고 `PENDING_RECONCILIATION`로 보수 수렴한다.
`physicalAttemptCount=0`인 채 lease/deadline이 끝났다면 provider send 전 실패가 증명되므로
`SUBMISSION_FAILED/NOT_SENT`다. 이전 generation의 worker는 현재 attempt
owner/generation CAS가 실패해 늦게 transport를 시작할 수 없다.

reconciliation의 positive match는 attempt에 결속된 sanitized provider correlation이
immutable action과 일치하는 경우를 우선한다. correlation이 없으면 first-byte 전에 완료된
`providerOrderBaselineRef`가 fresh·complete하고 `providerWriterPolicy=EXCLUSIVE_APP_WRITER`인
경우에만, baseline watermark에 없던 정확히 한 provider order가 full canonical immutable
action과 acceptance time(`sendStartedAt`·clock-skew 범위)을 모두 만족할 때
`SUBMITTED/ACKNOWLEDGED`로 전이한다. baseline에 이미 있던 동일 주문, 0개·복수 후보,
manual/external candidate, stale/partial baseline은 PENDING이다.

미접수 확정은 provider가 명시적으로 부재를 증명하거나,
공식 조회 의미상 완전한 continuation을 끝까지 소비하고 보수적 반영 지연이 지난
authoritative 주문·체결 조회가 전체 시간창에서 부재를 증명한 경우에만
`SUBMISSION_FAILED/CONFIRMED_NOT_ACCEPTED`를 허용한다. empty first page, partial
pagination, 조회 timeout/error, 반영 지연 전 empty는 증거가 아니다. provider가 권위 있는
negative를 제공하지 못하면 `PENDING_RECONCILIATION`과 active action fence를 유지하고
operator review로 보낸다. 이때 risk reservation도 release하지 않는다. 모호한 상태에서는
새 idempotency key, 새 intent, renew 모두 동일 action의 주문 권한을 만들 수 없다.

### 6.6 정정취소

`POST /api/v1/derivatives/mock/orders/{orderId}/cancel-or-modify`

Bearer 인증과 금융 safety write용 `X-Idempotency-Key`가 필수다. body는 다음 exact oneOf다.

```json
{
  "action": "MODIFY_REDUCE_QUANTITY",
  "newContracts": 1
}
```

또는:

```json
{
  "action": "CANCEL_REMAINING"
}
```

`MODIFY_REDUCE_QUANTITY`는 bool을 제외한 양의 `newContracts`가 authoritative remaining
quantity보다 작아야 한다. `CANCEL_REMAINING`에는 `newContracts`가 존재하면 안 된다.
`reason` 같은 unknown field도 거부한다. action별 versioned canonical request hash와
idempotency scope를 저장해 같은 key+semantic-equivalent body는 최초 결과를 replay하고,
한 field라도 다른 body는 `IDEMPOTENCY_CONFLICT`다.

두 action의 KIS provider mapping은 현재 `CANDIDATE_UNVERIFIED`다. 공식 detailed fixture에서
`ORD_PRCS_DVSN_CD`, `RVSE_CNCL_DVSN_CD`, `ORGN_ODNO`, `ORD_QTY`, `UNIT_PRICE`,
`NMPR_TYPE_CD`, `KRX_NMPR_CNDT_CD`, `RMN_QTY_YN`, `ORD_DVSN_CD`,
`FUOP_ITEM_DVSN_CD`의 exact 의미와 감소 수량 semantics를 고정하기 전에는
cancel/modify provider 호출을 만들지 않는다. sample 값을 추측해 `0`·blank·remaining
quantity를 교환하지 않으며 mapping version/source hash 승인 후에만 활성화한다.

requestId, actor, occurredAt은 요청 body가 아니라 공통 header, 인증 principal, 서버 clock에서
생성한다. 체결된 수량은 정정/취소 대상에서 제외한다.

v1 직접 정정은 current remaining quantity보다 작은 `newContracts`만 허용하고 symbol, side,
orderType, limitPrice, timeInForce는 바꾸지 않는다. 수량 증가와 가격·side·상품·order type
변경은 기존 주문의 안전한 취소가 권위 있게 확인된 뒤 새
candidate→evaluate→confirmation→submission으로 처리한다. 감소 정정도 current
open-order allocation과 risk reservation을 같은 hierarchical risk-ledger transaction에서
CAS하며, provider가 감소 수량을 명확히 ACK한 뒤에만 reservation의 미체결 capacity를
줄인다.

취소는 kill switch 중에도 위험감소 경로로 전송할 수 있지만, sanitized provider ACK 또는
완전한 authoritative 조회가 실제 취소된 미체결 수량을 확인하기 전에는 capacity를 release하지
않는다. 정정/취소 응답이 timeout·불명확이면 order 상태를
`PENDING_RECONCILIATION`으로 두고 기존 action fence와 최대 기존 reservation을 유지한다.
transport 자동 재시도와 새 주문 제출은 금지한다. partial fill과 cancel이 경합하면 체결 수량은
position capacity로 전환하고 provider가 확정한 취소 잔량만 release한다.

### 6.7 체결조회

`GET /api/v1/derivatives/mock/accounts/{accountId}/fills?from=2026-06-23&to=2026-06-23&limit=100&cursor=opaque`

`accountId`는 opaque 내부 ID이며 JWT subject 소유권을 조회 조건에 포함한다. provider
계좌번호는 요청·응답·로그에 노출하지 않는다. `from/to`는 KST date이고 inclusive 7일
이하, `limit` 기본/최대 100, stable order는 `(acceptedAt, orderId, fillId)` 오름차순이다.
응답은 `items`와 opaque `nextCursor`를 갖는다.

### 6.8 잔고현황

`GET /api/v1/derivatives/mock/accounts/{accountId}/positions?limit=20&cursor=opaque`

체결조회와 같은 owner-scope 규칙을 적용한다. `limit` 기본/최대 20, stable order는
`(productGroup, symbol, positionId)` 오름차순이고 응답은 `items/nextCursor`를 갖는다.
이 세 public query의 UI cursor는 사용자 표시용이다. authoritative reconciliation은
attempt에 결속된 별도 internal continuation state로 provider의 모든 page를 끝까지 소비하고
complete marker와 watermark를 검증하므로 client가 한 page만 읽은 사실을 미접수 증거로
사용하지 않는다.

### 6.9 Live-readiness

`GET /api/v1/derivatives/live-readiness`

```json
{
  "success": true,
  "data": {
    "enabled": false,
    "defaultState": "OFF",
    "requiredConditions": [
      "mockPerformanceReviewPassed",
      "accountQualificationConfirmed",
      "explicitUserConsentRecorded",
      "marginPolicyApproved",
      "providerOrderLifecycleMappingVerified",
      "authoritativeReconciliationVerified",
      "unsupportedPositionsAbsent",
      "operatorKillSwitchOff"
    ],
    "currentStatus": {
      "mockPerformanceReviewPassed": false,
      "accountQualificationConfirmed": false,
      "explicitUserConsentRecorded": false,
      "marginPolicyApproved": false,
      "providerOrderLifecycleMappingVerified": false,
      "authoritativeReconciliationVerified": false,
      "unsupportedPositionsAbsent": false,
      "operatorKillSwitchOff": true
    }
  }
}
```

`enabled`는 배포 immutable OFF gate, 운영자 account allowlist, 사용자 동의, margin policy,
order+cancel/modify+authoritative reconciliation mapping의 atomic 검증, unsupported position
0건, Kill Switch를 모두 결합한 read-only 결과다. 사용자 동의는 필요조건일 뿐 배포
gate·credential·allowlist를 변경하지 않으며, 공개 API로 이를 활성화할 수 없다.

---

## 7. Python gRPC 확장

### 7.1 DerivativeBrokerageService

```proto
service DerivativeBrokerageService {
  rpc GetDerivativeUniverse(GetDerivativeUniverseRequest) returns (GetDerivativeUniverseResponse);
  rpc CreateDerivativeMarginSnapshot(CreateDerivativeMarginSnapshotRequest) returns (CreateDerivativeMarginSnapshotResponse);
  rpc SubmitDerivativeMockOrder(SubmitDerivativeMockOrderRequest) returns (SubmitDerivativeMockOrderResponse);
  rpc ModifyOrCancelDerivativeOrder(ModifyOrCancelDerivativeOrderRequest) returns (ModifyOrCancelDerivativeOrderResponse);
  rpc GetDerivativeFills(GetDerivativeFillsRequest) returns (GetDerivativeFillsResponse);
  rpc GetDerivativePositions(GetDerivativePositionsRequest) returns (GetDerivativePositionsResponse);
  rpc StreamDerivativeOrderEvents(StreamDerivativeOrderEventsRequest) returns (stream DerivativeOrderEvent);
}
```

### 7.2 DerivativeMarketDataService

```proto
service DerivativeMarketDataService {
  rpc GetDerivativeQuote(GetDerivativeQuoteRequest) returns (GetDerivativeQuoteResponse);
  rpc GetDerivativeOrderBook(GetDerivativeOrderBookRequest) returns (GetDerivativeOrderBookResponse);
  rpc GetDerivativeDailySeries(GetDerivativeDailySeriesRequest) returns (GetDerivativeDailySeriesResponse);
}
```

FinancialEngineeringService는 P1의 BSM/Greeks/IV 계산 RPC를 재사용한다.
P2 adapter는 계산값과 함께 `outputKind`, `measure`, input provenance, assumptions,
validation status, units, `asOf/expiresAt`, model/data/source hash를 내부 evidence로 묶는다.
BSM은 European q 포함 `Q` 이론가이고, HMM은 causal 2-state WARN evidence이며, GBM은
`P` 가정의 stochastic scenario다. 어느 RPC 결과도 단독 주문 권한을 갖지 않는다. 실제
동적 헤지에는 이산 재조정, gamma/vega, jump/gap, 거래비용·유동성 잔여위험이 있음을
Dashboard/RAG 설명에 유지한다.

---

## 8. KIS TR ID 매핑

KIS TR ID와 모의 지원 경계는 이 표를 기준으로 한다. 구현 시 20260707 로컬 XLSX의 `API 목록` sheet와 `open-trading-api` 예제를 source manifest에 남긴다. S1.1에서는 이 표의 파생 API를 구현하지 않고, P2 feature flag가 켜질 때만 다시 확인한다.

### 8.1 사용 대상

| 기능 | Endpoint | 실전 TR ID | 모의 TR ID | P2 사용 |
|---|---|---|---|---|
| 선물옵션 시세 | `/uapi/domestic-futureoption/v1/quotations/inquire-price` | `FHMIF10000000` | `FHMIF10000000` | quote/stale guard |
| 선물옵션 시세호가 | `/uapi/domestic-futureoption/v1/quotations/inquire-asking-price` | `FHMIF10010000` | `FHMIF10010000` | spread/liquidity guard |
| 선물옵션 기간별시세 | `/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice` | `FHKIF03020100` | `FHKIF03020100` | trend/volatility context |
| 선물옵션 주문 | `/uapi/domestic-futureoption/v1/trading/order` | 주간 `TTTO1101U` | 주간 `VTTO1101U` | Mock order submit |
| 선물옵션 정정취소 | `/uapi/domestic-futureoption/v1/trading/order-rvsecncl` | 주간 `TTTO1103U` | 주간 `VTTO1103U` | modify/cancel |
| 선물옵션 주문가능 | `/uapi/domestic-futureoption/v1/trading/inquire-psbl-order` | `TTTO5105R` | `VTTO5105R` | margin snapshot 필수 입력 |
| 선물옵션 주문체결내역조회 | `/uapi/domestic-futureoption/v1/trading/inquire-ccnl` | `TTTO5201R` | `VTTO5201R` | fills reconciliation |
| 선물옵션 잔고현황 | `/uapi/domestic-futureoption/v1/trading/inquire-balance` | `CTFO6118R` | `VTFO6118R` | positions reconciliation |
| 선물옵션 실시간체결통보 | WebSocket | `H0IFCNI0` | `H0IFCNI9` | order event stream |

### 8.1.1 P1/P2 공유 호출·WebSocket budget

| 채널 | P2 계약 |
|---|---|
| REST Mock | appkey/account scope 합산 1 physical attempt/s, 최소 1,000ms no-burst. 현재가·호가·margin snapshot·주문·대사가 모두 같은 Redis 원자 queue를 사용 |
| REST Live-ready | 실전 계좌당 hard 18/s이지만 기본 120ms 간격을 유지한다. P2 실거래는 계속 OFF이며 read-only도 별도 gate가 필요 |
| `/oauth2/tokenP` | 일반 REST와 분리한 appkey credential scope 1/s + distributed singleflight/cache 재확인 |
| WebSocket session | 계좌(앱키)당 physical session 1개. 한 PC 다계좌는 appkey scope별 각 1개 가능 |
| WebSocket 등록 | P1/P2, 국내/해외, 주식/파생, 체결가/호가/예상체결/체결통보 합산 41개. `(TR_ID, tr_key)` 중복 dedupe, 42번째 사전 거부 |
| `/oauth2/Approval` | appkey scope 1/s + reconnect singleflight. 기존 session 종료와 generation fencing 후 ledger 복원 |

체결통보는 HTS ID 하나를 등록하면 연결된 모든 계좌 통보를 받으므로 계좌별로 중복 등록하지 않는다. “즉시 재호출”은 안전한 GET 라우팅 실패만 다음 quota slot에서 최대 1회 허용한다. 주문·정정·취소와 rate-limit 응답은 자동 재시도하지 않고 reconciliation으로 수렴한다.

### 8.2 모의 미지원 또는 P2 제외

| 항목 | 판단 |
|---|---|
| 야간 국내선물옵션 주문/정정취소/체결조회/주문가능 | 모의 미지원으로 주문 API에서 차단 |
| 선물옵션 증거금률 API | 모의 미지원. 주문가능조회, 잔고현황, 내부 margin buffer로 보수 처리 |
| 선물옵션 분봉조회 | 모의 미지원. P2 주문 판단에는 현재가, 호가, 기간별시세 중심 사용 |
| 해외선물옵션 주문 계열 | 모의 미지원. P2 범위 제외 |
| 파생 실거래 활성화 | 기본 OFF. live-readiness 조건 충족 전까지 차단 |

---

## 9. Fail-Closed 정책

아래 P2 오류 코드(`DERIVATIVE_GATE_CLOSED`, `MARGIN_GUARD_FAILED`)는 P1 API 명세서 2.3 오류 코드 표를 확장한다. 클라이언트 분기 규칙(HTTP 상태가 아니라 `error.code` 기준) 역시 P1과 동일하다.

| 조건 | API 처리 | Error Code |
|---|---|---|
| 상품군 allowlist 미포함 | 주문 후보 생성 또는 평가 단계에서 차단 | `DERIVATIVE_GATE_CLOSED` |
| 상품별 kill-switch 활성 | 평가 단계에서 차단 | `RISK_BLOCKED` |
| session이 `DAY`가 아닌 경우 | 주문 후보와 주문 제출 차단 | `DERIVATIVE_GATE_CLOSED` |
| 주문가능조회 실패 | `marginSnapshot` 생성 실패 | `MARGIN_GUARD_FAILED` |
| 주문가능수량 부족 | 평가 단계에서 차단 | `MARGIN_GUARD_FAILED` |
| margin buffer 부족 | 평가 단계에서 차단 | `MARGIN_GUARD_FAILED` |
| max 계약수 초과 | 평가 단계에서 차단 | `RISK_BLOCKED` |
| post-trade naked short call 노출 생성·증가의 2단계 확인 누락 | 주문 제출 단계에서 차단 | `RISK_BLOCKED` |
| 시장가가 청산/위험감소 주문에 해당하지 않음 | 평가 단계에서 차단 | `RISK_BLOCKED` |
| 시세/호가/owner position/margin/stress hard input stale | 평가 단계에서 보류 | `DATA_STALE` |
| BSM/Greeks/IV stale·assumption/validation 실패 | 해당 valuation evidence `ABSTAIN`+WARN. 단독 HOLD/BLOCK 금지 | 성공 envelope의 model warning |
| HMM low max-posterior/stale 또는 ML calibration drift — P2 component status 계약 승인 후 | 해당 model evidence `ABSTAIN`+WARN. HMM high entropy는 secondary diagnostic WARN이며 단독 HOLD/BLOCK 금지 | 성공 envelope의 model warning |
| HMM required component를 만들 수 없음 — 현재 P0 wire | 임의 state를 만들지 않고 Signal/RiskDecision success 생성 중단. 기존 service/schema-availability fail-closed | `PYTHON_SERVICE_UNAVAILABLE` → `HOLD` |
| 옵션 포지션 구조·loss profile을 재구성할 수 없음 | 후보 평가 또는 매도 제출 차단 | `RISK_BLOCKED` |
| post-trade naked short call 생성·증가의 deterministic stress snapshot 누락·stale | 평가 단계에서 보류 | `DATA_STALE` |
| post-trade naked short call 생성·증가의 deterministic stress가 활성 정책 한도 초과 | 평가 단계에서 차단 | `RISK_BLOCKED` |
| KIS Adapter 장애가 physical send 전에 확정 | `SUBMISSION_FAILED/NOT_SENT`, provider 호출 0건 | `BROKERAGE_UNAVAILABLE` |
| physical send 이후 timeout·응답 불명·claimed worker crash | `PENDING_RECONCILIATION`, 자동 재시도·expiry·renew 금지 | `BROKERAGE_UNAVAILABLE`과 order status 제공 |
| 같은 intent/decision의 다른 idempotency key 재제출 | state/unique 제약으로 두 번째 outbound 0건 | `IDEMPOTENCY_CONFLICT` 또는 기존 attempt 상태 |
| claim 전 Redis replay claim 실패 | DB submission claim과 provider 호출 0건, 주문 보류 | `BROKERAGE_UNAVAILABLE` |
| DB claim 뒤 quota slot을 `sendNotAfter` 전 확보하지 못함 | first-byte CAS 없이 `SUBMISSION_FAILED/NOT_SENT`, provider 호출 0건 | `BROKERAGE_UNAVAILABLE`과 terminal order status |
| stale worker·attempt generation 불일치 | first-byte CAS 실패, provider 호출 0건 | 기존 attempt 상태 replay |
| claim 뒤 Kill Switch/allowlist/account/session 변경 | serialized safety-gate generation/current-state 재검증 실패, `SUBMISSION_FAILED/NOT_SENT`, `physicalAttemptCount=0`, provider 호출 0건 | `RISK_BLOCKED` 또는 `DERIVATIVE_GATE_CLOSED`과 terminal order status |
| claim 뒤 principle/policy 또는 hard-data gate 변경 | aggregate safety-gate generation·active principle version·required data freshness 재검증 실패, `SUBMISSION_FAILED/NOT_SENT`, provider 호출 0건 | `RISK_BLOCKED` 또는 `DATA_STALE` |
| account가 `SHARED_EXTERNAL_WRITER` | 주문 candidate/evaluate/confirm/submit 차단, 조회만 허용 | `DERIVATIVE_GATE_CLOSED` |
| 서로 다른 action의 risk capacity 동시 소비 | hierarchical risk-ledger vector CAS에서 어느 scope든 한도를 넘는 reservation은 실패하고 intent/receipt를 원자 무효화, provider 호출 0건 | `RISK_BLOCKED` 또는 `MARGIN_GUARD_FAILED` |
| ambiguous positive 후보가 0개·복수·baseline 기존 주문·manual 가능 | 자동 ACK 금지, PENDING과 action/risk fence 유지 | `BROKERAGE_UNAVAILABLE`과 operator-review status |
| `EGW00201`/HTTP 429 | 자동 재시도 중단, queue/scope 점검 | `RATE_LIMITED` |
| WebSocket 두 번째 session/42번째 합산 등록 | provider 호출 전 거부, 기존 ledger 유지 | `RATE_LIMITED` 또는 `CONFLICT` |

---

## 10. 테스트 기준

| 테스트 | 확인 |
|---|---|
| universe/master activation test | synthetic·unverified master와 KOSPI200 밖 product group은 discovery만 가능하고 `orderable=false`; verified source/version/hash/freshness/tick/session fixture 없이는 후보·provider 호출 0건 |
| provider symbol/tick test | 실제 option symbol은 approved official master format·right/maturity/strike와 exact match하고 synthetic·unmapped code·off-tick/out-of-band price는 반올림 없이 거부 |
| kill-switch test | 상품군/상품별 kill-switch가 신규 주문을 차단하는지 확인 |
| session guard test | validated KRX calendar와 상품별 시간으로 휴장·야간·`now>=lastTradingAt`을 차단하고 일반일 15:45와 최종거래일 15:20을 구분 |
| margin snapshot failure test | KIS 주문가능조회 실패 시 주문 평가가 진행되지 않는지 확인 |
| margin buffer test | buffer 부족 시 `MARGIN_GUARD_FAILED`가 반환되는지 확인 |
| option short confirmation test | server-reconstructed post-trade naked short call 노출 생성·증가만 2단계 확인을 요구. long sell-to-close와 short buy-to-close 분류·receipt oneOf 검증 |
| option loss profile test | long option은 premium+cost 손실상한, naked short call은 `UNBOUNDED/maxLossAmount=null`; short put·covered·spread·multi-leg는 v1 `CANDIDATE_DISABLED` |
| exposure separation test | premium, notional, conservative delta exposure, market value, margin, stress loss가 통화·단위·asOf·source와 함께 분리 직렬화 |
| hard delta provenance test | hard exposure는 BSM delta와 무관한 contract-bound call `+1`/put `-1`, method/source hash를 요구하고 missing/mismatch면 HOLD |
| model authority test | BSM/Greeks/IV/HMM/GBM 결과 하나로 ALLOW/HOLD/BLOCK 또는 계약수가 바뀌지 않음 |
| expectation/measure test | `P` expected payoff와 discounted `Q` no-arbitrage price를 교환하거나 단일 경로 보장값으로 표현하는 경우 0건 |
| BSM measure/assumption test | `Q/THEORETICAL_PRICE`, European, q 포함, `tau=valuation→lastTradingAt`, `N(d)` 비물리확률 문구와 provenance 확인 |
| BSM numerical test | 15:20 last-trading fixture 독립 oracle, q 포함 put-call parity, 가격 경계·단조성, finite-difference Greeks, Vega/Rho 1%p와 theta calendar-day 단위 확인 |
| BSM domain test | `tau<=0`, `sigma<=0`, non-finite/비양수 S·K는 `ABSTAIN/VALUATION_DOMAIN_INVALID`, calculation/Greek key 0건 |
| model evidence status test | European valid=`AVAILABLE`, stale/numeric failure=`ABSTAIN`, non-European=`UNSUPPORTED`; status별 required/forbidden key oneOf와 null/omitted 혼용 거부 |
| IV validation test | European 무차익 경계 밖이면 bisection 0회와 `IV_NOT_BRACKETED`; `CALIBRATION_IDENTITY` code, quote/solver/r-q provenance, round-trip과 독립 fair-value 검증 분리 |
| HMM causal/abstain test | future suffix 변경 시 prefix posterior 불변, active state `RISK_ON/OFF`, low max-posterior는 state 위조 없이 내부 ABSTAIN, entropy `>0.95`만 warning이고 equality는 warning 아님 |
| HMM wire-boundary test | 현재 P0 required component 부재는 임의 enum 없이 `PYTHON_SERVICE_UNAVAILABLE → HOLD`; P2 `status=ABSTAIN` success는 별도 contract change 전 직렬화 0건 |
| lifecycle test | 8-state 전이와 claim 전 expiry/invalidation의 exact terminationReason을 검증. EXPIRED 역전이 0건, client 선언 무시 |
| renew test | allowlisted terminal EXPIRED reason만 같은 logicalActionId·증가 generation의 새 DRAFT를 생성. terminal provider attempt 뒤 새 주문은 새 logicalActionId/generation 1; 이전 evidence 복사 0건 |
| confirmation eligibility test | `HOLD|BLOCK`, synthetic/unorderable master, partial mandatory stress set은 confirmation-view/receipt 성공 0건. `POST_ACTIVATION_WIRE_SHAPE_ONLY` fixture를 실행 가능한 accepted response로 취급하지 않음 |
| receipt TTL test | 일반 submission과 naked-short 2단계 receipt 만료가 decision과 모든 hard snapshot의 최소 만료시각 이하이고, 상태에 맞는 EVALUATED/SUBMIT_READY→EXPIRED CAS와 receipt 무효화가 원자적이며 provider 호출 0건 |
| valuation optionality test | valuation unsupported/ABSTAIN만으로 hard guard를 생략하거나 주문을 승인·차단하지 않고, present ID만 audit event에 결속 |
| model calibration/drift test | raw LightGBM score가 confidence로 노출되지 않고 calibration·alpha decay·capacity/crowding 실패 시 ABSTAIN |
| deterministic stress test | long put premium+fee, terminal short-call gap+spread/slippage×2+fee, margin shortfall tagged result가 독립 oracle과 일치하고 required probability null. mandatory fat-tail/cluster/crisis-correlation/liquidity/crowding set 누락은 HOLD, optional Q vol×2 누락은 hard block 아님 |
| hedge disclosure test | delta hedge 설명에 discrete rebalancing, gamma/vega, jump/gap, 비용·유동성 잔여위험 포함 |
| confirmation view TOCTOU test | 응답의 canonical disclosure 전체를 독립 canonicalize해 expected digest fixture와 대조. 렌더 뒤 position/margin/stress/template/locale가 바뀌면 receipt 0건·새 view 요구 |
| confirmation two-step lifecycle test | step1 POST가 step1 view를 소비하며 별도 step2 view·전체 disclosure·별도 hash를 원자 발급. step2가 step1 receipt와 step2 view를 각각 한 번 소비하고 최종 receipt를 발급하며, 같은 view/hash 재사용·부분 commit은 0건 |
| confirmation receipt replay test | 다른 subject/order의 receipt, 만료·재사용·역순 receipt가 차단되고 provider 호출이 0건인지 확인 |
| submission claim race test | submit과 expiry/다른 worker가 경합해도 receipt+DB idempotency scope+decision+active action fence+`SUBMISSION_CLAIMED` transaction 하나만 성공하고 outbound 최대 1건 |
| idempotency namespace test | DB unique scope와 request compare 열 분리. NFC/key order/decimal `2.4=2.40` semantic replay, omitted/null/0·unknown/one-field difference는 conflict |
| different idempotency key test | 같은 intent/decision에 서로 다른 key를 동시에 보내도 DB state/unique 제약으로 outbound 합계 1건 |
| cross-intent action fence test | active fence 동안 outbound 합계 1건. EXPIRED renew는 같은 logicalActionId+증가 generation, terminal provider attempt 뒤 새 주문은 새 logicalActionId/generation 1과 새 확인 필요 |
| canonical action hash test | exact KIS mapping version/code projection 결속. LIMIT canonical decimal, MARKET limitPrice required null+UNIT_PRICE `"0"`, bool contracts/omitted/null/0 ambiguity 거부 |
| hash-key rotation test | PENDING/active receipt/idempotency row의 old key version을 유지하고 모든 non-retired digest와 비교해 rotation 중 동일 action fence 우회 0건 |
| confirmation disclosure hash test | before/after position, positionEffect/riskReductionOnly, risk vector, market applicability, template/version/content hash/locale, monetary units, tagged stress metric/amount/formula/source 변경은 기존 receipt 무효화 |
| confirmation lineage equality test | disclosure의 동일 ID principle·margin·exposure·stress 값과 source/input hash는 canonical stored record의 normalized projection과 exact match. 같은 ID의 값 불일치·template same-version overwrite는 view 발급 0건 |
| cross-action risk reservation test | 서로 다른 strike/side/price intent가 같은 position snapshot·margin을 병렬 소비해도 ordered ledger-vector CAS와 reservation 때문에 합산 contracts/margin/exposure/stress 한도를 넘는 outbound 0건 |
| cross-product portfolio limit race test | 서로 다른 productGroup/strategy intent가 동시에 claim해도 `ACCOUNT_GLOBAL→PRODUCT_GROUP→STRATEGY` lock order와 exact vector CAS로 account-global margin/exposure/daily-loss/MDD 한도 초과 outbound 0건 |
| hard-evidence ledger binding test | margin/exposure/stress snapshot과 decision의 ordered `riskLedgerVersionVector`가 exact match하지 않으면 receipt/claim/provider 호출 0건 |
| reservation lifecycle test | PENDING은 reservation 유지, ACK는 open-order allocation, partial fill은 position+remaining open으로 split, reject/not-sent/confirmed absence/cancel 미체결은 원자 release |
| reduce-order position race test | short 1계약에 MARKET REDUCE 두 intent가 경합할 때 reducible inventory reservation은 하나만 성공. 첫 fill 뒤 둘째 count 0 reservation은 invalidated/NOT_SENT되어 신규 long 전환 outbound 0건 |
| send deadline test | DB claim 뒤 quota queue에서 `sendNotAfter`를 넘기면 `SUBMISSION_FAILED/NOT_SENT`, `physicalAttemptCount=0`, provider 호출 0건 |
| stale worker fencing test | lease/generation 교체 뒤 이전 worker의 first-byte CAS가 실패하고, 현재 generation만 `physicalAttemptCount=0→1`을 한 번 성공 |
| claim-after-kill-switch race test | first-byte CAS와 Kill Switch/allowlist/account gate update가 같은 DB row에서 직렬화되고, gate update가 먼저 commit되면 count 0·provider 호출 0건. send-start가 먼저 commit되면 count 1 attempt 하나만 대사 |
| claim-after-policy/freshness race test | active principle version 또는 required hard-data invalidation과 first-byte CAS가 aggregate safety-gate row에서 직렬화되고, 변경 commit 뒤 count 0 attempt outbound 0건 |
| exclusive-writer gate test | dedicated Mock account의 operator evidence가 `EXCLUSIVE_APP_WRITER`가 아니면 모든 P2 write가 차단되고 내부 reservation만으로 external concurrency 안전을 주장하지 않음 |
| ambiguous submission test | `physicalAttemptCount=1` 뒤 timeout·parse 실패·worker crash는 `PENDING_RECONCILIATION`; transport retry·renew·새 동일 action outbound 0건 |
| submission outcome test | `physicalAttemptCount=0`은 `SUBMISSION_FAILED/NOT_SENT`, typed reject는 `DEFINITIVELY_REJECTED`, ack는 `SUBMITTED/ACKNOWLEDGED`; event와 intent state exact match |
| owner scope test | 다른 사용자의 account/order/decision/snapshot ID가 `NOT_FOUND`이고 provider 호출이 0건인지 확인 |
| market order guard test | 신규 진입 시장가 차단, 위험감소 시장가 조건부 허용 확인 |
| unsupported order-shape test | `ROLL`, `CONDITION_LIMIT`, short put 생성/증가, covered/spread/multi-leg는 `CANDIDATE_DISABLED`. activation scan에 unsupported position이 있으면 P2 write 전체 HOLD·provider 호출 0건 |
| instrument profile oneOf test | FUTURE는 option/premium/coverage/max-loss field를 규정된 null/NOT_APPLICABLE로, OPTION은 option profile로 직렬화. 선물 margin을 max loss로 표현하지 않고 모든 성공 snapshot이 finite/typed 규칙 충족 |
| stale data test | 시세/호가/position/margin/stress stale은 HOLD, model valuation stale은 evidence ABSTAIN+WARN으로 분리되는지 확인 |
| reconciliation test | explicit correlation 또는 fresh complete baseline+exclusive writer+baseline 이후 exact 1개 immutable-action/time match만 `SUBMITTED/ACKNOWLEDGED`. 이전 동일 주문·0/복수/manual 후보는 PENDING. 명시적 negative 또는 authoritative complete absence만 `SUBMISSION_FAILED/CONFIRMED_NOT_ACCEPTED`; empty first page·partial page·timeout은 PENDING+fence/reservation 유지 |
| reduce-modify reservation test | 미체결 수량 감소만 허용하고 명확한 provider ACK 뒤 reservation 감소. 수량 증가·가격/side/상품/orderType 변경은 fresh candidate/evaluate/confirm 필요 |
| cancel/modify body oneOf test | `CANCEL_REMAINING`은 newContracts 금지, `MODIFY_REDUCE_QUANTITY`는 bool 제외 양의 값이 authoritative remaining보다 작음; unknown field와 같은 key 다른 body conflict |
| ambiguous cancel test | timeout·불명확 cancel은 기존 최대 reservation+fence 유지, authoritative cancelled quantity만 release. partial fill 경합은 filled position 전환과 cancelled remainder release 합계가 원수량과 일치 |
| bounded pagination test | universe 100, fills 100/7일, positions 20 cap·stable cursor를 검증하고 UI partial page를 authoritative reconciliation complete로 사용하지 않음 |
| order audit reference test | request/idempotency hash key version, decision, safety gate/risk vector/hard snapshot이 event에 있고 일반 option flow와 post-trade naked short increase receipt oneOf가 배타적. raw key/model value 권한 승격 0건 |
| live-readiness test | 기본 OFF와 필수 조건 미충족 시 실거래 활성화 불가 확인 |
| derivative async event default-off test | P2 feature flag OFF이면 내부 async event가 발행되지 않는지 확인 |
| derivative async event status test | 외부에는 Spring 상태 조회 API만 노출되는지 확인 |
| provider evidence security test | raw KIS body/header/account/token/key 없이 allowlisted normalized field와 sanitized receipt/hash만 남는지 확인 |
| shared REST quota test | P1/P2 병행과 retry를 포함해 mock physical send 간격이 1,000ms 이상이며 주문/대사가 backfill보다 우선하는지 확인 |
| WebSocket aggregate budget test | P1/P2 합산 41개까지 허용, 42번째와 두 번째 session은 outbound 0건, 중복 `(TR_ID, tr_key)`는 한 건으로 계산하는지 확인 |
| Approval singleflight test | 동시 접속키 miss가 1 physical issue로 합쳐지고 재연결 중 기존 session을 중복 생성하지 않는지 확인 |

---

## 11. Source Manifest

P2 구현 산출물은 다음 근거를 artifact manifest에 남긴다. 각 entry에는 exact URL/로컬 source
hash, `verifiedAt`, claim scope, license/access note를 함께 기록한다.

| Source | Evidence/adoption class | 용도와 채택 경계 |
|---|---|---|
| 한국투자증권 모의투자 안내 | `OFFICIAL_REPORT` | 국내 선물옵션 모의거래 흐름 근거 |
| 한국투자증권 수수료 안내 | `OFFICIAL_REPORT` | 비용 config와 수수료/세금 출처 |
| KIS Developers API 호출 유량 안내(2026-04-20 기준) | `OFFICIAL_REPORT` | 실전 18/s, 모의 1/s, tokenP/Approval 1/s, WebSocket 1세션·합산 41등록 근거 |
| `한국투자증권_오픈API_전체문서_20260707_030000.xlsx` | `OFFICIAL_REPORT` | endpoint, TR ID, 모의 지원 경계 |
| KIS 공식 `open-trading-api/examples_user/domestic_futureoption/domestic_futureoption_functions.py` | `UPSTREAM_ONLY` → `ADOPTED_WITH_OVERRIDE` | 주문가능·주문·정정취소·잔고현황 workflow 탐색. endpoint/TR ID/필드는 공식 XLSX/portal로 재검증한 항목만 프로젝트 보안·quota·approval 계약으로 덮어써 채택 |
| KIS 공식 `open-trading-api/examples_llm/domestic_futureoption` | `UPSTREAM_ONLY` → `ADOPTED_WITH_OVERRIDE` | 주문가능 필드와 검증 check 탐색. raw sample response, secret/account 노출, live-order/retry 기본값은 채택 금지 |
| KIS 공식 GitHub `stocks_info` 국내 선물옵션 master | `UPSTREAM_ONLY` → `CANDIDATE_UNVERIFIED` | symbol/type/strike/month discovery 후보. sample parser의 TLS 검증 우회는 `REJECTED_UNSAFE_DEFAULT`; fixed-origin verified collector와 KRX 상품별 enrichment·hash fixture 전 orderable 금지 |
| [Black and Scholes 1973](https://doi.org/10.1086/260062), [Merton 1973](https://doi.org/10.2307/3003143), [Nobel 1997 공식 설명](https://www.nobelprize.org/prizes/economic-sciences/1997/press-release/) | `PRIMARY_RESEARCH` / `OFFICIAL_REPORT` | 무차익·복제·European BSM과 합리적 옵션 가격 일반화 근거. q 포함식은 canonical fixture로 별도 검증하며 실제 방향확률로 해석 금지 |
| [SEC GME Staff Report](https://www.sec.gov/files/staff-report-equity-options-market-struction-conditions-early-2021.pdf) | `OFFICIAL_REPORT` | 옵션시장 구조 보강. GME gamma squeeze 단일원인 주장을 지지하지 않음 |
| [BIS OTC derivatives](https://data.bis.org/topics/OTC_DER) | `OFFICIAL_REPORT` | notional, gross market value, gross credit exposure 구분 |
| [FINRA Options](https://www.finra.org/investors/investing/investment-products/options) | `OFFICIAL_REPORT` | long option premium 손실상한과 naked short call의 이론상 무제한 손실 |
| [KRX KOSPI 200 Futures 상품 명세](https://global.krx.co.kr/contents/GLB/02/0201/0201040201/GLB0201040201.jsp) | `OFFICIAL_REPORT` | KOSPI200 선물 multiplier·tick·정규/최종거래일 15:20·cash settlement 대조 |
| [KRX KOSPI 200 Options 상품 명세](https://global.krx.co.kr/contents/GLB/02/0201/0201040202/GLB0201040202.jsp) | `OFFICIAL_REPORT` | KOSPI200 옵션의 exercise·settlement·multiplier 등 contract master 대조. 실제 주문 전 현재 KIS/거래소 명세 재검증 |
| [Grossman–Stiglitz 1980](https://www.aeaweb.org/aer/top20/70.3.393-408.pdf) | `PRIMARY_RESEARCH` | 정보비용과 완전 정보효율 사이의 긴장만 직접 뒷받침. alpha decay·capacity·crowding threshold는 프로젝트 model-risk gate이며 이 논문의 직접 결론으로 과장하지 않음 |
| [Ho et al., Denoising Diffusion Probabilistic Models, NeurIPS 2020](https://proceedings.neurips.cc/paper/2020/hash/4c5bcfec8584af0d967f1ab10179ca4b-Abstract.html) | `PRIMARY_RESEARCH` / `RESEARCH_ONLY` | DDPM의 학습된 reverse denoising process 개념 근거. 금융 Brownian/GBM diffusion과 동일 모델·API라고 주장하지 않음 |
| [Rabiner 1989](https://doi.org/10.1109/5.18626) | `PRIMARY_RESEARCH` | HMM forward recursion·filtering 수학 근거. 프로젝트는 prefix-only causal 구현과 별도 fixture로 검증 |
| [hmmlearn API](https://hmmlearn.readthedocs.io/en/stable/api.html) | `OFFICIAL_REPORT` | Viterbi/MAP decoder와 API 경계를 확인하는 구현 문서. 직접 causal filter의 수학적 원전으로 대신하지 않음 |
| [Veritasium 공식 영상 페이지](https://www.veritasium.com/videos/2024/2/28/the-trillion-dollar-equation) | `SECONDARY_EDUCATIONAL` | 옵션·확률과정·헤지·BSM·HMM·diffusion을 연결하는 discovery/설명 보조. 수치 oracle·사건 원인·provider 계약으로 사용 금지 |
| 사용자 제공 한국어 학습 Markdown, `sha256:8f33e2a2282e6c5d2b85aee79e641f9c2e98b0aec7a769e0b486530a2cab0099` | `SECONDARY_EDUCATIONAL`; `verifiedAt=2026-07-17` | 184줄·28,443 bytes인 content-addressed local source. 로컬 경로·원문·이미지는 public 문서에 싣지 않고 concept discovery에만 사용 |
| `ACT/365F`, Vega/Rho `/100`, calendar Theta `/365` | `ILLUSTRATIVE`; `conventionStatus=PROJECT_CONVENTION` | 외부 근거 등급이 아니라 wire 단위와 fixture 재현을 위한 내부 계약. 독립 수치 test와 source hash로 검증하며 시장 전체의 유일 표준으로 주장하지 않음 |

사용자 제공 Veritasium 한국어 학습 Markdown과 영상은 `SECONDARY_EDUCATIONAL` discovery
source다. tracked 문서에 로컬 경로·원문·이미지를 복사하지 않고, numerical oracle·provider
계약·특정 사건 원인의 gold evidence로 사용하지 않는다.
