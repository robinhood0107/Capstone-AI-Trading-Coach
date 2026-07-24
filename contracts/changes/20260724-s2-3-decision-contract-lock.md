# KR: S2.3 Decision API 계약 잠금과 stored-source 경계

## 변경 이유

S0.2의 현행 주식·ETF/ETN `OrderIntent`는 `estimatedPrice`를 유일한 가격 필드로
고정했지만, S2.2 snapshot/hash 구현과 API 설명 일부가 `limitPrice`를 사용했다. 이 상태에서는
같은 요청을 schema는 허용하고 evaluator는 표현하지 못하거나, 서로 다른 payload가 같은
`semanticInputHash`로 축약될 수 있다. production Decision route와 저장 decision이 아직 없으므로
호환 shim을 추가하지 않고 런타임 구현 전에 breaking cleanup을 완료한다.

또한 S2.3 판단 경로에는 저장된 현재가·호가와 KIS_MOCK 잔고를 읽는 canonical source가 없다.
S2.3이 provider HTTP를 호출하거나 0/빈 값으로 운영 데이터를 꾸미지 않도록, producer와 consumer
소유권을 분리한 append-only sanitized observation 경계를 먼저 고정한다.

## 현물 v1 OrderIntent와 hash

- 현물 v1 `orderIntent`의 exact 필드는
  `symbol,side,orderType,quantity,estimatedPrice,estimatedAmount,timeframe,strategyId`다.
- `price`, `limitPrice`, `userId`, `accountId`, `mode`는 허용하지 않는다.
- MARKET과 LIMIT 모두 `estimatedPrice`를 사용한다. `orderType`은 주문 방식이고 가격 필드명을
  바꾸는 discriminator가 아니다.
- `estimatedPrice`와 `estimatedAmount`는 양의 원화 정수이며
  `estimatedAmount == quantity * estimatedPrice`를 overflow 없는 exact integer 연산으로
  검증한다. 반올림이나 scale 보정으로 불일치를 수용하지 않는다.
- 파생 P2 `derivativeOrderIntent.limitPrice`와 S3 내부 provider `UNIT_PRICE` mapping은 별도
  namespace다. 두 계약은 현물 v1 HTTP/hash 필드에 `limitPrice`를 다시 추가하는 근거가 아니다.
- `HASH-CANONICALIZATION-S22-V2`와 `s2.2-metric-snapshot-v2`가 V1을 대체한다. V2 hash에는 위
  8개 필드가 모두 들어간다. `snapshotArtifactHash`는 같은 V2 artifact exact UTF-8 bytes를
  hash한다.
- V1 hash를 받아들이거나 V1/V2를 동시에 계산하는 compatibility path는 두지 않는다. 배포된
  route와 저장 decision이 없으므로 과거 Git artifact만 역사 증거로 남는다.

## Stored observation 소유권

| source | canonical storage/read model | producer owner | S2.3 권한 |
|---|---|---|---|
| 현재가·호가 | append-only `market_quote_observations`와 latest sanitized projection | S1.1 market-data 후속 producer | owner 비의존 bounded `SELECT` only |
| KIS_MOCK 잔고 | append-only `portfolio_balance_observations` + `portfolio_position_observations` | S3 KIS_MOCK read-side producer | JWT owner + `KIS_MOCK` predicate의 `SELECT` only |
| INTERNAL_PAPER 잔고 | 기존 `paper_accounts` + `paper_positions`의 한 SQL owner-scoped projection | S3 INTERNAL_PAPER ledger | JWT owner + ACTIVE 단일 context `SELECT` only |
| 공시 | V6 sanitized PostgreSQL observations를 읽는 loopback `GetDisclosureEvents` | S1.6 collector | gRPC read only |

V9는 source row를 seed하지 않고 S2.3에 source INSERT/UPDATE/DELETE 권한을 주지 않는다. 후속
S1.1/S3 producer는 별도 migration과 별도 non-superuser role로 필요한 INSERT만 받아야 한다.
KIS account number, provider payload/header/token은 observation, log, metric, audit, outbox에
저장하지 않는다. account scope가 필요하면 purpose/version HMAC의 64-hex opaque hash만 사용한다.

현재가·호가 observation은 symbol, 양의 원화 정수 price/bid/ask, UTC `observedAt/receivedAt`,
schema/source version, lowercase 64-hex `sourceRef`와 artifact hash를 가진다. KIS_MOCK balance
observation은 owner, source, cash/equity, completeness, position count, UTC 시각, version/hash를
가지고 position rows는 symbol별 nonnegative quantity/market value만 가진다. list/문자열 상한을
넘거나 중복 symbol, partial pagination, invalid hash, future timestamp가 있으면 truncate하거나
추정하지 않는다.

저장 row가 없거나 stale/incomplete이면 typed unavailable이며 정상적인 persisted HTTP 200
`HOLD`가 된다. production seed, 0원 잔고, 빈 position을 “성공 source”로 만드는 fallback은
금지한다. test fixture는 test source set/profile과 Testcontainers 안에서만 INSERT할 수 있다.
따라서 S1.1/S3 producer가 아직 배포되지 않은 환경은 거짓 ALLOW를 만들지 않고 HOLD-only로
동작한다.

## Markdown과 생성물 drift 방지

- 공개 규범 문서는 이 변경 기록과 `contracts/schemas/order_intent.schema.json`,
  `contracts/openapi/openapi.json`을 따른다.
- CI는 Decision API/S2.2 hash를 설명하는 규범 Markdown에서 현물 `limitPrice`와 stale
  `contracts/openapi/api.openapi.yaml` 경로를 거부한다.
- 파생 P2 문서는 `derivativeOrderIntent` namespace를 명시해 허용한다.
- `private-reference/repo/**`, `private-reference/evidence/**`, frozen worktree mirror와 과거 실행
  prompt는 비규범 역사 자료다. 내용을 재작성하지 않고, 현재 규범 문서의 supersession 표지만
  사용한다.

## 보안·운영 결과

- JWT subject만 owner다. source selector에 user/account를 받지 않으며 cross-owner read는
  no-row로 수렴한다.
- source read와 평가에는 persistence transaction을 열지 않는다. decision/audit/outbox/
  idempotency 저장 transaction에서 Principle current version을 다시 조건부 확인한다.
- provider HTTP, live account, live order, broker publish는 이 변경에서 모두 0이다.

# EN: S2.3 Decision API contract lock and stored-source boundary

## Reason

The current cash-equity `OrderIntent` contract has one price field, `estimatedPrice`, but part of
the S2.2 snapshot/hash implementation and API prose used `limitPrice`. That split could make a
schema-valid request unrepresentable by the evaluator or collapse distinct payloads into the same
semantic hash. No production Decision route or persisted decision exists, so this change performs
the breaking cleanup before runtime and adds no compatibility shim.

S2.3 also lacks canonical stored sources for a current quote and KIS_MOCK balance. The approved
boundary is an append-only sanitized observation contract with separate producers and consumers,
not provider HTTP or invented zero/empty production values inside S2.3.

## Locked decisions

- The exact cash-equity v1 fields are
  `symbol,side,orderType,quantity,estimatedPrice,estimatedAmount,timeframe,strategyId`.
  `price`, `limitPrice`, user/account identifiers, and `mode` are rejected.
- MARKET and LIMIT both use `estimatedPrice`. Positive integer KRW values must satisfy
  `estimatedAmount == quantity * estimatedPrice` by exact overflow-checked arithmetic.
- P2 `derivativeOrderIntent.limitPrice` and the S3 provider `UNIT_PRICE` mapping are separate
  namespaces and do not change the cash-equity API/hash field.
- `HASH-CANONICALIZATION-S22-V2` and `s2.2-metric-snapshot-v2` replace V1 and hash all eight
  order-intent fields. Historical V1 artifacts remain in Git history only.
- S1.1 owns future writes to `market_quote_observations`; S3 owns future KIS_MOCK balance and
  INTERNAL_PAPER ledger writes. S2.3 has bounded read-only adapters.
- V9 creates no production source seed and gives `decision_app` no source mutation privilege.
  Missing, stale, incomplete, future-dated, malformed, or over-limit source data fails closed.
- A missing expected source is a persisted HTTP 200 HOLD. Invariants, malformed serialization,
  authorization failures, and database commit failures remain technical failures with no decision
  side effect.
- Test source rows exist only in test profiles/Testcontainers. Provider HTTP, live-account,
  live-order, and broker-publish calls remain zero.
