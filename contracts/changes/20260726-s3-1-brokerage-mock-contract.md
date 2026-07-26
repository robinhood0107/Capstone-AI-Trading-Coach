# S3.1 Brokerage Mock 주문 계약 변경

## KR

이 변경은 S2.3 Decision runtime과 S2.4 Kill Switch를 소비하는 S3.1 KIS Mock 주문
제출/조회/취소와 stored balance/buyable 계약을 고정한다. 구현 범위는
`POST /api/v1/brokerage/mock/orders`, `GET /api/v1/brokerage/orders/{orderId}`,
`POST /api/v1/brokerage/orders/{orderId}/cancel`,
`GET /api/v1/brokerage/mock/accounts/{accountId}/balances`,
`GET /api/v1/brokerage/mock/accounts/{accountId}/buyable`이며, 체결·live readiness는 후속 S3
계약까지 계획 상태로 남긴다.

- 요청 body는 `decisionId`, exact 8-field `orderIntent`,
  `userAcknowledgement.warningsAccepted`만 허용한다.
- 클라이언트가 제출한 `accountId`, provider, actor, raw receipt 필드는 unknown field로 거부한다.
- raw `X-Idempotency-Key`, raw 계좌번호, provider raw payload는 저장하지 않고
  purpose-version HMAC과 sanitized canonical result만 저장한다.
- V11 ledger는 one Decision = one order, idempotency replay/conflict, FORCE RLS owner projection,
  append-only order event, cancel-request event와 sanitized outbox를 강제한다.
- balance/buyable은 stored `KIS_MOCK` projection만 읽고 opaque `accountId`로 owner scope를
  검증한다.
- Spring/Python gRPC Brokerage boundary는 codegen과 shared-secret/circuit-breaker wiring을 두되
  기본 disabled 상태이며 raw credential/account 필드를 싣지 않는다.
- 만료 Decision은 `DECISION_EXPIRED`, Kill Switch 또는 invalidation은 `RISK_BLOCKED`,
  verified tick-table context가 없는 LIMIT 주문은 `BROKERAGE_UNAVAILABLE`로 닫는다.
- S3.1의 KIS Mock adapter 검증은 injected/fake transport만 사용하며
  provider/live account/broker/order physical call은 0건이다.

## EN

This change locks the S3.1 KIS Mock order submit/query/cancel and stored
balance/buyable contract that consumes S2.3 Decision runtime output and S2.4 Kill
Switch authority. Runtime scope is `POST /api/v1/brokerage/mock/orders`,
`GET /api/v1/brokerage/orders/{orderId}`, `POST /api/v1/brokerage/orders/{orderId}/cancel`,
`GET /api/v1/brokerage/mock/accounts/{accountId}/balances`, and
`GET /api/v1/brokerage/mock/accounts/{accountId}/buyable`; fills and live readiness
remain planned follow-up S3 contracts.

- The request body only accepts `decisionId`, the exact 8-field `orderIntent`, and
  `userAcknowledgement.warningsAccepted`.
- Body-supplied `accountId`, provider, actor, and raw receipt fields are rejected as
  unknown fields.
- Raw `X-Idempotency-Key`, raw account identifiers, and provider raw payloads are not
  persisted; only purpose-version HMACs and sanitized canonical results are stored.
- The V11 ledger enforces one Decision = one order, idempotency replay/conflict, FORCE
  RLS owner projection, append-only order events, cancel-request events, and sanitized
  outbox rows.
- Balance/buyable reads use stored `KIS_MOCK` projections only and verify owner scope
  through the opaque `accountId`.
- The Spring/Python gRPC Brokerage boundary is generated and wired with shared-secret
  and circuit-breaker settings, but remains disabled by default and carries no raw
  credential/account fields.
- Expired Decisions fail with `DECISION_EXPIRED`; Kill Switch or invalidation fails with
  `RISK_BLOCKED`; LIMIT orders without verified tick-table context fail closed with
  `BROKERAGE_UNAVAILABLE`.
- S3.1 validates the KIS Mock adapter with injected/fake transport only. Provider,
  live-account, broker, and live-order physical calls remain 0.

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
