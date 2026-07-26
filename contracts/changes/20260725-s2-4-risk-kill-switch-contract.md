# S2.4 Risk API and Kill Switch contract lock

- Status: accepted
- Accepted on: 2026-07-26
- Issue: #46
- Runtime owner: `workspaces/decision-platform/spring-api/`
- OpenAPI SSOT: `contracts/openapi/openapi.json`
- Provider calls authorized by this contract: 0

## KR

### 목적과 경계

이 변경은 S2.4의 `GET /api/v1/risk/portfolio`,
`GET /api/v1/risk/kill-switch`, `POST /api/v1/risk/kill-switch` 계약을
잠근다. `GET /api/v1/risk/assets/{symbol}`은 응답 계약과 producer가 준비되지
않았으므로 이번 세션에서 구현하거나 OpenAPI에 추가하지 않는다.

S2.4는 KIS, broker, gRPC 또는 외부 HTTP를 호출하지 않는다. legacy
`risk_snapshots`는 사용하거나 삭제하지 않으며 producer가 생기는 후속 세션에서
재평가한다.

### P0-1. DB 단일 원본과 generation

Kill Switch 원본은 `risk_kill_switch`의 `kill_switch_id='GLOBAL'` 단일
행이다. 상태 조회, Decision 평가 guard와 후속 주문 guard는 Redis,
`@Cacheable`, JVM/static cache 없이 매 요청 DB를 읽는다. 실제 상태 변경마다
양의 `generation bigint`를 단조 증가시키며, 판단과 후속 mutation 사이에는
boolean 값이 아니라 generation을 재검증한다. 같은 상태 요청은 200 no-op이며
generation과 transition을 늘리지 않는다.

### P0-2. 비대칭 권한과 해제 재검증

인증된 USER와 ADMIN은 활성화할 수 있다. 해제는 ADMIN만 가능하다. 해제
transaction 안에서 현재 `users.status`, `users.role`,
`users.security_version`을 다시 읽어 JWT actor와 비교한다.

- 비활성 계정 또는 security version 불일치: `UNAUTHORIZED`(401), write 0건
- 현재 role이 ADMIN이 아님: `FORBIDDEN`(403), write 0건
- 같은 observed generation으로 경합: `CONFLICT`(409), 자동 재시도 0건

### P0-3. 사용자 projection과 reason

USER/ADMIN 상태 응답의 data key는 정확히 `active`, `reasonClass`,
`changedAt`이다. `changedBy`, actor user ID, request ID, generation과 자유
서술 reason은 반환하지 않는다. 요청 reason은 최대 200자의 제어문자 없는
bounded 문자열이며 quote/comment/boolean-expression 형태의 injection 신호도
거부한다. 서버는 아래 low-cardinality enum으로만 매핑하며 원문은 DB, audit,
outbox, log, metric에 저장하지 않는다.

- `USER_MANUAL_STOP`
- `OPERATOR_MANUAL_STOP`
- `DATA_FRESHNESS_STOP`
- `BROKERAGE_FAILURE_STOP`
- `DEMO_SAFETY_STOP`
- `ADMIN_RESUME`
- `INITIAL_STATE`

`POST` body는 `active`와 선택적 `reason`만 허용하며 unknown property는
`VALIDATION_ERROR`다. `X-Idempotency-Key`는 필수다.

### P0-4. append-only Decision 무효화

`decisions`의 V9 append-only 계약과 `read_decision_owner_projection()`의
signature를 변경하지 않는다. 별도 `decision_invalidations` 테이블은
composite FK `(decision_id, evaluation_id)`, owner RLS/FORCE RLS와
`UNIQUE (decision_id, reason_class)`를 가진다. `decision_app`은 직접
INSERT/UPDATE/DELETE/TRUNCATE하지 못한다.

Kill Switch 활성화 transaction은 fixed-search-path `SECURITY DEFINER`
함수로 유효하고 미소비된 모든 owner의 Decision을 한 번의 집합 INSERT로
무효화한다. 같은 reason 재실행은 conflict를 흡수한다. 조회는 별도
`read_decision_usability()` projection을 사용한다. 만료는
`DECISION_EXPIRED`(409), 무효화는 `RISK_BLOCKED`(422)로 구분한다.

### P0-5. portfolio source of truth

`/risk/portfolio`는 S2.3의 owner-scoped stored observation projection과
`MetricSnapshotAssembler`를 재사용한다. 새 assembler를 만들지 않고
`risk_snapshots`를 읽지 않는다. 다른 owner의 source row는 DB actor scope와
projection에서 0행으로 수렴한다.

### P0-6. 없는 source를 꾸미지 않는다

구조 또는 row가 없는 값은 0, false 또는 임의 production 값으로 채우지 않는다.
`var95`, `cvar95`, `hmmRegime`, `hmmRegimeProbability`,
`realizedVolatility20d`는 producer가 생기기 전까지 null이다.
`portfolioValue`, `dailyPnlRate`, `mdd`,
`annualizedVolatility20d`도 실제 stored observation row가 없으면 null이다.
freshness는 실제 `observedAt`/`freshUntil`로만 계산하고 source 부재는
sanitized `MISSING_SOURCE` warning으로 노출한다. `killSwitchActive`는 매
요청 DB 단일 행에서 읽는다.

### 원자성과 최소권한

상태 변경은 `FOR UPDATE` 직렬화, generation CAS, transition INSERT,
활성화 시 전역 Decision 무효화, bounded audit INSERT, event outbox INSERT를
한 transaction에 둔다. transaction 안에서 provider, gRPC, 외부 HTTP,
Redis 또는 metric/log를 호출하지 않는다. metric/log는 commit이 끝난 뒤에만
실패 비전파 방식으로 기록한다.

`risk_kill_switch_transitions`와 `decision_invalidations`는 append-only다.
사용자 projection은 actor를 노출하지 않는다. ADMIN actor audit은 현재 DB
ADMIN 재검증 뒤 bounded definer projection으로만 읽는다. collector, worker,
source writer와 disclosure reader에는 새 권한을 주지 않는다.

### 리뷰 후 고정한 구현 불변식

- Decision 저장은 Kill Switch singleton을 shared lock으로 다시 확인한다. 따라서
  진행 중인 Decision이 먼저 commit된 뒤 활성화 transaction에서 무효화되거나,
  활성화가 먼저 commit되어 Decision 저장이 `RISK_BLOCKED`로 끝나며 활성화와
  저장 사이의 사용 가능한 Decision 누락 구간을 만들지 않는다.
- 전이 시각은 singleton lock을 얻은 뒤 DB 시각으로 계산하고 직전
  `changed_at`보다 작아지지 않게 저장한다. request ID는 correlation 값일 뿐
  전역 유일 식별자가 아니므로 nullable이고 재사용해도 전이를 막지 않는다.
- `priceFresh`는 잔고 revision을 대용하지 않고
  `latest_market_quote_observations`의 실제 `observedAt`/`freshUntil`만 사용한다.
- 모든 금융 write의 멱등성 키는 공통 정책의 16~128자 허용 alphabet을 따르고,
  배포 설정은 이 상한을 더 낮출 수만 있다.
- Decision 무효화 조회는 `(valid_until, decision_id)` 인덱스로 전역 scan을
  제한한다. JSON Schema는 active/reason 조합, 정수 원화 값과 비율 범위를
  fail-closed하며 OpenAPI 오류 응답은 `success=false`, `data=null`인 전용
  envelope만 허용한다.

## EN

### Purpose and boundary

This change locks the three S2.4 routes: portfolio risk read, Kill Switch
read, and Kill Switch mutation. The asset-risk route is deferred until its
response contract and producers exist. S2.4 makes zero KIS, broker, gRPC, or
external HTTP calls. It neither reads nor drops the legacy `risk_snapshots`
table.

### Accepted decisions

1. The singleton `GLOBAL` database row is the uncached authority. Every guard
   reads it and compares a monotonically increasing generation.
2. USER and ADMIN can stop. Only a currently active ADMIN with a matching
   security version can resume, rechecked inside the write transaction.
3. Public state data contains exactly `active`, `reasonClass`, and
   `changedAt`. Free-form reason text is never stored. Actor identifiers are
   excluded from public state, outbox payloads, logs, and metric labels; they
   appear only in the bounded ADMIN audit projection.
4. Decisions remain append-only. A separate owner-protected append-only
   invalidation table and bounded definer projection express usability.
5. Portfolio risk reuses the S2.3 stored-observation projections and the
   existing `MetricSnapshotAssembler`; it never reads `risk_snapshots`.
6. Missing producers and rows remain null with sanitized degraded warnings.
   Production values are never synthesized.

The state-change transaction serializes the singleton row, performs a
generation CAS, appends the transition, invalidates eligible Decisions in one
set operation, appends the bounded audit record and writes the outbox event.
No external call or cache operation occurs inside the transaction.

### Post-review invariants

- Decision persistence takes a shared lock on the Kill Switch singleton and
  rechecks that it is inactive. An in-flight Decision therefore either commits
  before activation and is invalidated by that transaction, or is rejected
  after activation.
- Transition time is obtained after the singleton lock and remains monotonic
  against the stored database timestamp. Request IDs are nullable correlation
  values, not globally unique transition identifiers.
- `priceFresh` is derived only from the latest persisted market quote. The
  shared financial-write idempotency policy accepts 16 to 128 characters, while
  deployment configuration may choose a lower upper bound.
- The invalidation lookup is indexed by validity horizon. Conditional state
  schemas, integer/range constraints, and fail-closed OpenAPI error envelopes
  prevent impossible or success-shaped error payloads.

## Contract artifacts

- `contracts/schemas/s2-4-kill-switch-state.schema.json`
- `contracts/schemas/s2-4-kill-switch-request.schema.json`
- `contracts/schemas/s2-4-risk-portfolio.schema.json`
- positive fixtures under `contracts/examples/`
- negative fixtures under `contracts/examples/invalid/`

Existing `contracts/validate.py` discovers these schemas and fixtures by name,
so no parallel validator or second contract source is introduced.
