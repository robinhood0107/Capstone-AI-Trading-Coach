# S3.1 Brokerage Mock 주문 계약 변경

## KR

이 변경은 S2.3 Decision runtime과 S2.4 Kill Switch를 소비하는 S3.1 KIS Mock 주문
제출/조회/취소와 stored balance/buyable 계약을 고정한다. 구현 범위는
`POST /api/v1/brokerage/mock/orders`, `GET /api/v1/brokerage/orders/{orderId}`,
`POST /api/v1/brokerage/orders/{orderId}/cancel`,
`GET /api/v1/brokerage/mock/accounts/{accountId}/balances`,
`GET /api/v1/brokerage/mock/accounts/{accountId}/buyable`이며, 체결·live readiness는 후속 S3
계약까지 계획 상태로 남긴다. 아래 S3-online 추가 계약은 이 offline-first 기준을
폐기하지 않고, 명시적으로 닫힌 KIS_MOCK online 경계를 additive하게 확장한다.

- 요청 body는 `decisionId`, exact 8-field `orderIntent`,
  `userAcknowledgement.warningsAccepted`만 허용한다.
- 클라이언트가 제출한 `accountId`, provider, actor, raw receipt 필드는 unknown field로 거부한다.
- raw `X-Idempotency-Key`, raw 계좌번호, provider raw payload는 저장하지 않고
  purpose-version HMAC과 sanitized canonical result만 저장한다.
- V11 ledger와 additive V12 보안 경계는 one Decision = one order, idempotency
  replay/conflict, owner projection, append-only order event, cancel-request event와 sanitized
  outbox를 강제한다.
- `decision_app`은 `orders`, `order_events`, capability digest table을 직접 읽거나 쓰지 않는다.
  별도 raw capability를 prepared bind로 전달하는 bounded `SECURITY DEFINER` 함수만 실행하며,
  Flyway에는 capability의 lowercase SHA-256 digest만 전달한다.
- 주문 함수는 `risk_kill_switch` row를 잠그고 서비스가 관측한 generation을 다시 비교한 뒤
  Decision/actor/order intent를 검증하고 order/event/audit/outbox를 한 transaction에서 쓴다.
  order lifecycle은 `(order_id, event_seq)` unique와 exact event type/status pair로 고정한다.
- S3.1 기본 경로의 balance/buyable은 stored `KIS_MOCK` projection만 읽고 opaque
  `accountId`로 owner scope를 검증한다.
- Spring/Python gRPC Brokerage boundary는 codegen과 shared-secret/circuit-breaker wiring을 두되
  기본 disabled 상태이며 raw credential/account 필드를 싣지 않는다.
- Brokerage proto generator는 repository-relative dirfd, `O_NOFOLLOW`, regular-file 검사와
  atomic replace를 사용해 output 또는 ancestor symlink를 따라가지 않는다.
- 만료 Decision은 `DECISION_EXPIRED`, Kill Switch 또는 invalidation은 `RISK_BLOCKED`,
  verified tick-table context가 없는 LIMIT 주문은 `BROKERAGE_UNAVAILABLE`로 닫는다.
- 원래 S3.1의 KIS Mock adapter 검증은 injected/fake transport만 사용하며
  provider/live account/broker/order physical call은 0건이다.

### S3-online 추가 계약

- online transport는 `KIS_MOCK`과 공식 mock HTTPS origin만 허용한다. 주문
  `VTTC0011U | VTTC0012U`, 전량 취소 `VTTC0013U`, 잔고 `VTTC8434R`, 매수가능
  `VTTC8908R`, 최근/과거 체결 `VTTC0081R | VTSC9215R`의 exact path/TR 조합 외에는
  client 생성 또는 send 전에 거부한다. `KIS_LIVE` 주문·정정·취소 TR은 구현·allowlist·
  설정에 없고 계속 OFF다.
- 모든 mock REST 호출은 S1.1의 Redis `kis:rest:v3:{opaque-scope}` 1초 no-burst
  limiter를 공유하고 `/oauth2/tokenP`는 deployment-global 1초 limiter를 공유한다.
  Redis 또는 limiter 실패는 outbound 전에 닫고 주문성 호출의 자동 retry와 redirect는 0이다.
- provider 호출 전 DB에 durable reservation을 만들고 V15는 성공한 mock 접수를
  `ACCEPTED`로 원자 기록한다. 모호한 transport/기록 실패는
  `PENDING_RECONCILIATION` 기록을 시도하고, 그 보조 기록도 실패하면 최초 `SUBMITTED`
  reservation을 recovery anchor로 남긴다.
  raw provider order/account 값은 Spring·DB·응답으로 넘기지 않으며, 취소에 필요한 reference는
  owner/order에 결속된 Fernet ciphertext로 Redis에 60초~7일만 보관한다.
  provider send 전 encrypted `PENDING` marker를 `SET NX`로 확정하고 접수 reference만
  `COMMITTED`로 원자 전환한다. commit 실패 시 pending을 recovery anchor로 남기고 in-memory
  reference로 전량취소를 retry 없이 최대 1회 보상한다.
- online balance/buyable은 저장된 owner/account anchor가 먼저 확인된 경우에만 gRPC를 통해
  읽는다. body·query·gRPC caller는 계좌번호, origin, transport, token, limiter를 덮어쓸 수
  없다. Python gRPC는 numeric loopback, bounded deadline/message/concurrency, shared secret,
  finite physical cap과 credential별 단일 `KIS_MOCK_BOUND_ACCOUNT_ID`를 요구한다. 모든
  RPC account는 이 binding과 일치해야 하며
  `KIS_MOCK_BROKERAGE_ONLINE_ENABLED=false`가 기본이다.
- 일회성 online 최종 검증의 승인 경로는
  `balance -> buyable -> LIMIT BUY 1주 -> 전량 취소 -> 최근 체결조회` exact `FULL` probe다.
  packet은 최종 local/remote HEAD, PR #55 required CI, 같은 HEAD의 clean security report
  digest, 60분 이하 TTL, Redis PTTL baseline, 물리 cap `tokenP=1`/`brokerage=5`, retry 0,
  artifact 0을 결속한다. absolute regular file·owner·mode `0600`·`O_NOFOLLOW`, clean
  worktree, packet account/bound account 일치, canonical
  SHA-256과 현재 사용자의 별도 approval ID/SHA latch가 모두 맞아야 실행된다.
- `FULL` 실패 뒤 stable 출력만으로 exact leaf를 식별할 수 없으면 같은 5단계를 재실행하지 않는다.
  새 final HEAD/CI/security evidence에 결속한 `probeType=BALANCE_DIAGNOSTIC`,
  `steps=["balance"]`, cap `tokenP=1`/`brokerage=1`, retry/artifact 0 packet과 현재 사용자의
  새 exact 승인으로 production transport·limiter·balance parser를 1회만 실행한다. 이 profile은
  order reference key·주문 gateway·취소·체결조회를 만들지 않는다. 실패 출력은 allowlisted
  `reasonCode`, 선택적 HTTP status와 `[A-Z0-9_-]{1,32}` provider code만 허용하고
  body/header/URL/`msg1`/계좌/credential을 버린다. diagnostic도 single-use이며 성공 뒤 최종
  5단계에는 별도의 새 `FULL` packet과 새 exact 승인이 필요하다.
  balance parser는 cash/equity/position source shape만 검증하고 margin requirement나
  gold ETF/ETN 분류를 합성하지 않는다. trusted enrichment가 없는 persistent online
  balance projection은 provider 호출 전에 fail-closed한다.
- exact probe는 packet 검증 뒤 runtime factory를 만들기 전에 `approvalId`와 canonical
  SHA-256에서 파생한 opaque Redis key를 `SET NX PX`로 claim한다. claim은 성공·첫 실패·
  runtime 생성 실패 뒤에도 packet TTL까지 유지되며 Redis 장애나 이미 존재하는 claim은 provider
  handoff 전에 fail-closed한다. KIS_MOCK response body는 shared credential scrubber의 JSON
  parse/sanitize 전에 1 MiB cap으로 중단한다.
- exact probe는 background polling, gRPC server 상시 활성화, S3.3 fill writer/DB append를
  승인하지 않는다. gRPC online server를 켜는 작업은 별도 bounded operator approval 없이는
  수행하지 않는다. 구현·fixture·OpenAPI·일반 테스트의 provider physical call은 계속 0이다.

## EN

This change locks the S3.1 KIS Mock order submit/query/cancel and stored
balance/buyable contract that consumes S2.3 Decision runtime output and S2.4 Kill
Switch authority. Runtime scope is `POST /api/v1/brokerage/mock/orders`,
`GET /api/v1/brokerage/orders/{orderId}`, `POST /api/v1/brokerage/orders/{orderId}/cancel`,
`GET /api/v1/brokerage/mock/accounts/{accountId}/balances`, and
`GET /api/v1/brokerage/mock/accounts/{accountId}/buyable`; fills and live readiness
remain planned follow-up S3 contracts. The additive S3-online contract below
preserves this offline-first default while introducing an explicitly closed
KIS_MOCK online boundary.

- The request body only accepts `decisionId`, the exact 8-field `orderIntent`, and
  `userAcknowledgement.warningsAccepted`.
- Body-supplied `accountId`, provider, actor, and raw receipt fields are rejected as
  unknown fields.
- Raw `X-Idempotency-Key`, raw account identifiers, and provider raw payloads are not
  persisted; only purpose-version HMACs and sanitized canonical results are stored.
- The V11 ledger plus the additive V12 security boundary enforce one Decision = one
  order, idempotency replay/conflict, owner projection, append-only order events,
  cancel-request events, and sanitized outbox rows.
- `decision_app` cannot directly read or write `orders`, `order_events`, or the
  capability digest table. It can only execute bounded `SECURITY DEFINER` functions
  with a separately injected raw capability prepared bind; Flyway receives only its
  lowercase SHA-256 digest.
- The order function locks `risk_kill_switch`, compares the generation observed by the
  service, validates the Decision, actor, and order intent, then writes
  order/event/audit/outbox evidence in one transaction. `(order_id, event_seq)`
  uniqueness and exact event type/status pairs fix lifecycle ordering.
- The default S3.1 balance/buyable path uses stored `KIS_MOCK` projections only and
  verifies owner scope through the opaque `accountId`.
- The Spring/Python gRPC Brokerage boundary is generated and wired with shared-secret
  and circuit-breaker settings, but remains disabled by default and carries no raw
  credential/account fields.
- The Brokerage proto generator uses repository-relative dirfds, `O_NOFOLLOW`,
  regular-file checks, and atomic replacement so output or ancestor symlinks are
  never followed.
- Expired Decisions fail with `DECISION_EXPIRED`; Kill Switch or invalidation fails with
  `RISK_BLOCKED`; LIMIT orders without verified tick-table context fail closed with
  `BROKERAGE_UNAVAILABLE`.
- The original S3.1 validation uses injected/fake transport only. Provider,
  live-account, broker, and live-order physical calls remain 0.

### Additive S3-online contract

- The online transport accepts only `KIS_MOCK`, the official mock HTTPS origin, and
  exact path/TR pairs for cash order, full cancel, balance, buyable, and recent/archive
  execution reads. No `KIS_LIVE` order, modify, or cancel TR is implemented, allowlisted,
  or configurable.
- Mock REST calls share the S1.1 Redis one-second no-burst limiter; `/oauth2/tokenP`
  shares its deployment-global one-second limiter. Redis/limiter failures close before
  outbound, and order-like calls have zero automatic retries and no redirects.
- A durable database reservation is created before provider handoff, and V15 atomically
  records a confirmed mock acceptance as `ACCEPTED`. Ambiguous transport or
  outcome-persistence failures attempt `PENDING_RECONCILIATION`; if that auxiliary write
  also fails, the original `SUBMITTED` reservation remains the recovery anchor. Raw
  provider/account values never cross into Spring, the database, or responses. Cancel
  references are owner/order-bound Fernet ciphertext in Redis with a bounded 60-second
  to seven-day TTL. An encrypted `PENDING` marker is stored with `SET NX` before the send
  and atomically becomes `COMMITTED` only after a valid receipt. A commit failure keeps
  the pending recovery anchor and attempts at most one no-retry full-cancel compensation.
- Online balance/buyable is reachable only after a stored owner/account anchor succeeds.
  Callers cannot override the account number, origin, transport, token, or limiter.
  Python gRPC requires numeric loopback, bounded deadlines/messages/concurrency, a shared
  secret, finite caps, and one credential-bound `KIS_MOCK_BOUND_ACCOUNT_ID`. Every RPC
  account must match that binding; `KIS_MOCK_BROKERAGE_ONLINE_ENABLED=false` is the default.
- The final one-shot online verification authority is the exact five-step `FULL`
  `balance -> buyable -> LIMIT BUY quantity 1 -> full cancel -> recent execution read`
  probe. Its packet binds final local/remote HEAD, PR #55 required CI, a clean same-HEAD
  security report digest, a TTL of at most 60 minutes, Redis PTTL baselines, caps
  `tokenP=1` and `brokerage=5`, retry 0, artifact 0, a protected absolute 0600 file, a
  canonical digest, a clean worktree, an exact packet-account/bound-account match, and a
  separate current-user approval ID/SHA latch.
- When a `FULL` failure cannot be narrowed by the stable output, the same five steps are
  not rerun. A new same-HEAD/CI/security-bound `BALANCE_DIAGNOSTIC` packet with only
  `steps=["balance"]`, caps `tokenP=1` and `brokerage=1`, retry 0, artifact 0, and a new
  exact current-user approval runs the production transport, limiter, and balance parser
  once. It does not construct the order-reference store, order gateway, cancel, or
  execution-read path. Failure output is limited to an allowlisted `reasonCode`, optional
  HTTP status, and an optional `[A-Z0-9_-]{1,32}` provider code; bodies, headers, URLs,
  `msg1`, account data, and credentials are discarded. The diagnostic packet is
  single-use, and success still requires another new `FULL` packet and approval. Its
  balance parser validates only bounded cash/equity/position source shape; it never
  fabricates margin or gold ETF/ETN risk fields, and persistent online balance fails
  closed before provider access without trusted enrichment.
- After packet validation and before runtime construction, the exact probe claims an
  opaque Redis key derived from `approvalId` and the canonical SHA-256 using `SET NX PX`.
  The claim remains consumed through success, first failure, and runtime-construction
  failure until packet expiry; Redis failure or an existing claim rejects before provider
  handoff. KIS_MOCK response bodies are capped at 1 MiB before JSON parse/sanitize in the
  shared credential scrubber.
- The exact probe does not authorize background polling, persistent gRPC enablement, or
  S3.3 fill-writer/database append. Enabling the online gRPC server requires a separate
  bounded operator approval. Implementation, fixture, OpenAPI, and ordinary test calls
  remain at zero provider physical calls.

## Verification

```bash
uv run --frozen python -m unittest discover -s contracts/tests -v
uv run --frozen python contracts/validate.py
workspaces/decision-platform/spring-api/gradlew \
  -p workspaces/decision-platform/spring-api --no-daemon ktlintCheck build
workspaces/decision-platform/spring-api/gradlew \
  -p workspaces/decision-platform/spring-api --no-daemon prepareOpenApiFixtureEnv
uv run --frozen python contracts/run_openapi_gate.py \
  --env-file workspaces/decision-platform/spring-api/build/openapi-fixture/openapi.env
```
