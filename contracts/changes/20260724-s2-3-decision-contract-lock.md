# KR: S2.3 Decision API 계약 잠금과 stored-source 경계

> Instrument source approval amendment: 2026-07-24
>
> Current catalog SHA-256:
> `d035607af50a0f7cb9cd7170e9a6a188e6af32d5bbbdb76e5e4f7b3edc68cd18`
>
> Superseded SHA-256:
> `58e55ebda0154a079cff3d5c2527da66743cf3fdeeaf063b86b23b581371fab3`,
> `58b658a1482b378d5a7c8c394381a14b6ad6e41c222d2f84e4edec65c1ab1e6f`

## 변경 이유

S0.2의 현행 주식·ETF/ETN `OrderIntent`는 `estimatedPrice`를 유일한 가격 필드로
고정했지만, S2.2 snapshot/hash 구현과 API 설명 일부가 `limitPrice`를 사용했다. 이 상태에서는
같은 요청을 schema는 허용하고 evaluator는 표현하지 못하거나, 서로 다른 payload가 같은
`semanticInputHash`로 축약될 수 있다. 이 결정을 잠글 당시 `origin/main`에는 production
Decision route와 저장 이력이 없었고, S2.3 isolated branch에서 처음 구현하므로 호환 shim을
추가하지 않고 breaking cleanup을 완료한다.

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
| 현재가·호가 | append-only `market_quote_observations`와 latest sanitized projection | S1.1 market-data offline producer | owner 비의존 bounded `SELECT` only |
| 종목 분류·상품 위험 | append-only `instrument_catalog_observations`와 `latest_instrument_catalog_observations` | S1.1 instrument catalog offline producer | symbol exact predicate + 최대 1행 bounded `SELECT` only |
| KIS_MOCK 잔고 | append-only `portfolio_balance_observations` + `portfolio_position_observations` | S3 KIS_MOCK read-side offline producer | JWT owner + `KIS_MOCK` predicate의 `SELECT` only |
| INTERNAL_PAPER 잔고 | 기존 `paper_accounts` + `paper_positions`의 한 SQL owner-scoped projection | S3 INTERNAL_PAPER ledger | JWT owner + ACTIVE 단일 context `SELECT` only |
| 결정적 리스크·일일 주문 수 | append-only `deterministic_risk_observations` + `daily_order_count_observations` | deterministic source offline producer | JWT owner + 직전 거래일/coverage predicate `SELECT` only |
| 종목↔corp_code | append-only `corporation_registry_observations` + exact current projection | S1.6 collector offline producer | Python gRPC server bounded `SELECT` |
| 공시 | V6 sanitized PostgreSQL observations를 읽는 loopback `GetDisclosureEvents` | S1.6 collector | gRPC read only |

V9는 source row를 seed하지 않고 S2.3에 source INSERT/UPDATE/DELETE 권한을 주지 않는다.
이번 review remediation은 S1.1/S3/deterministic/S1.6 소유 모듈에 fixture/mock transport 기반
offline producer와 별도 non-superuser writer role의 exact INSERT만 추가한다.
KIS account number, provider payload/header/token은 observation, log, metric, audit, outbox에
저장하지 않는다. account scope가 필요하면 purpose/version HMAC의 64-hex opaque hash만 사용한다.

현재가·호가 observation은 symbol, 양의 원화 정수 price/bid/ask, UTC `observedAt/receivedAt`,
schema/source version, lowercase 64-hex `sourceRef`와 artifact hash를 가진다. KIS_MOCK balance
observation은 owner, source, cash/equity, completeness, position count, UTC 시각, version/hash를
가지고 position rows는 symbol별 nonnegative quantity/market value만 가진다. list/문자열 상한을
넘거나 중복 symbol, partial pagination, invalid hash, future timestamp가 있으면 truncate하거나
추정하지 않는다.

`S23_INSTRUMENT_SOURCE_APPROVED`에 따라 S1.1은 instrument catalog의 유일한 producer owner다.
row는 `symbol`, `isEtfEtn`, `isGoldEtfEtn`, nullable `productRiskScore`, `catalogVersion`,
UTC `observedAt/receivedAt`, lowercase 64-hex `sourceRef`와 `artifactHash`를 저장한다.
`isGoldEtfEtn=true`이면 `isEtfEtn=true`여야 하고 risk score가 있으면 `0..1` 범위다.
`decision_market_writer`는 두 S1.1 append-only table에 exact INSERT만 가지며 UPDATE/DELETE/
TRUNCATE/SELECT는 가지지 않는다. latest projection은 symbol별 시각·ID 순서의 정확히 최신 row를
제공하고 S2.3 reader는 exact symbol + `LIMIT 1`로만 소비한다. 미래 timestamp, row 부재, nullable
risk score를 `isEtfEtn=false`, `isGoldEtfEtn=false` 또는 0으로 합성하지 않는다.

canonical table/projection, production bean/port, offline fixture producer, 최소권한 writer,
bounded reader, freshness/completeness, no-fake test 중 하나라도 빠진 구조적 부재는
`S23_RUNTIME_SOURCE_BLOCKED`다. 구조가 갖춰진 뒤 특정 평가 시점의 row 부재,
stale/incomplete/future timestamp 또는 transient DB failure는 typed unavailable이며 정상적인
persisted HTTP 200 `HOLD`다. production seed, 0원 잔고, 빈 position, synthetic
`marginRequirement=0`/`isGoldEtfEtn=false`를 “성공 source”로 만드는 fallback은 금지한다.
test fixture는 test source set/profile과 Testcontainers 안에서만 INSERT할 수 있다.

## Review remediation amendment

- `GetDisclosureEvents`의 빈 `corp_code`는 Python server가 sanitized corporation registry에서
  symbol을 exact-one으로 resolve한다. 0개/복수는 incomplete이며 임의 선택하지 않는다.
- 공시 repository는 `as_of - 365일`부터 exact 365일 경계를 포함하고 365일+1은 제외한다.
  `report_nm`은 event identity에 관여하지 않는다.
- gRPC는 숫자 loopback plaintext, reflection/retry/transparent retry 없음, physical attempt
  최대 1, concurrency 8, source/total/hard deadline 500/900/2,000ms와 256KiB/1MiB 상한을
  강제한다. Python repository는 pool 8, acquisition 450ms, connect 1초이고 cancellation은 실행
  중 query/connection을 취소·해제한다. event/source-ref/response 상한 초과와
  `RESOURCE_EXHAUSTED`/구조적 `DATA_LOSS`는 truncate나 typed unavailable이 아닌 technical
  failure다.
- Spring source coordinator는 queue 없는 8개 worker에서 source 500ms와 전체 evaluation 900ms의
  남은 예산을 함께 사용한다. 전체 예산 만료 뒤 새 physical call을 만들지 않고 timeout task를
  cancel하며 request trace MDC를 worker 실행 뒤 복원한다. JDBC connection/statement도 500ms
  안에 끝나야 한다. KIS_MOCK balance/position/margin은 같은 immutable source revision만
  조립한다.
- persistence transaction은 idempotency advisory lock → 동일 owner/ACTIVE/current Principle의
  `FOR SHARE OF principle` 재검증 → Decision graph INSERT 순서다. updater-first mismatch는
  409/all writes zero, decision-first는 updater가 Decision commit까지 대기한다.
- Decision child row는 `decision_id + evaluation_id` composite FK로 같은 graph를 강제하고 audit
  target은 payload `decisionId`와 같아야 한다. offline writer replay는 primary/alternate unique
  identity와 모든 의미 필드가 같을 때만 no-op이며 mismatch는 `23505`로 전체 transaction을
  rollback한다. 일일 주문 수가 해당 거래일을 evaluation 시각까지 완전히 덮으면
  `freshUntil=evaluationAsOf+10분`으로 pin한다.
- `decision_app`은 NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOBYPASSRLS다. broad/future default
  SELECT를 제거하고 Decision/audit/outbox/idempotency base read를 금지한다. idempotency replay는
  fixed-search-path SECURITY DEFINER bounded function이 scope hash, owner scope hash, expiry를
  모두 확인한다.
- timer/counter/log는 commit 뒤 fault-isolated다. 정상 path에서는 exact once이며 registry/filter/
  appender 실패가 이미 commit된 Decision의 원래 200 projection을 뒤집지 않는다.

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
  idempotency 저장 transaction에서 Principle current version을 `FOR SHARE`로 다시 확인한다.
- provider HTTP, live account, live order, broker publish는 이 변경에서 모두 0이다.

## 구현·drift 상태

- canonical catalog는 `contracts/catalogs/s2-3-decision-contract.v1.json`이며 SHA-256은
  `d035607af50a0f7cb9cd7170e9a6a188e6af32d5bbbdb76e5e4f7b3edc68cd18`다. review
  `58e55ebda0154a079cff3d5c2527da66743cf3fdeeaf063b86b23b581371fab3`와 provisional
  `58b658a1482b378d5a7c8c394381a14b6ad6e41c222d2f84e4edec65c1ab1e6f`는 위 amendment로
  superseded된 역사 증거다.
- tracked `contracts/openapi/openapi.json`은 같은 digest를
  `x-s2-3-contract-sha256`으로 기록하고 Decision path 3개와 `S23*` component 5개만 허용한다.
- V9는 Decision/trace/artifact/audit/outbox/idempotency를 한 transaction에 append하고
  application role의 UPDATE/DELETE/TRUNCATE, unrelated table, Flyway history와 schema DDL을
  거부한다.
- 저장 현재가·instrument catalog·KIS_MOCK 잔고·결정적 risk/order-count·corp registry offline
  producer는 각
  S1.1/S3/deterministic/S1.6 소유 모듈의 S2.3 prerequisite다. 구조가 완성된 뒤 row가 없는
  평가는 200 HOLD이며 S2.3이 fake production row나 provider fallback으로 이를 숨기지 않는다.

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
- S1.1 owns offline writes to `market_quote_observations` and
  `instrument_catalog_observations`; S3 owns offline KIS_MOCK balance
  observation writes and later INTERNAL_PAPER ledger mutation. Deterministic and S1.6 modules own
  risk/order-count and corporation/disclosure observations. S2.3 has bounded read-only adapters.
- An instrument row stores `symbol`, ETF/ETN and gold classification, nullable product risk score,
  catalog version, observed/received time, source reference, and artifact hash. The market writer
  has INSERT only, and the S2.3 reader returns at most one exact-symbol latest row without a
  synthetic false/zero fallback.
- V9 creates no production source seed and gives `decision_app` no source mutation privilege.
  Missing, stale, incomplete, future-dated, malformed, or over-limit source data fails closed.
- Missing structural source machinery is `S23_RUNTIME_SOURCE_BLOCKED`. Once the machinery exists,
  a missing or transiently unavailable observation is a persisted HTTP 200 HOLD. Invariants,
  malformed serialization, authorization failures, and database commit failures remain technical
  failures with no decision side effect.
- Source execution uses no queue, at most eight workers, a 500 ms per-source budget, and one shared
  900 ms evaluation budget. It starts no new physical call after expiry and cancels timed-out work.
  JDBC acquisition/statements are capped at 500 ms. The Python reader uses an eight-connection
  pool, 450 ms acquisition, and one-second connect budget. Bounds exhaustion and structural data
  loss are technical failures, not truncated success or typed unavailability.
- KIS_MOCK balance, positions, and margin must share one immutable revision. Decision children use
  a composite `decision_id + evaluation_id` foreign key, and the audit target must match its payload
  decision ID. An offline writer replay is a no-op only when every semantic field matches; a
  primary or alternate-identity mismatch aborts the full transaction with PostgreSQL `23505`.
- A daily-order-count observation that completely covers its trading day through the evaluation
  time pins `freshUntil` to `evaluationAsOf + 10 minutes`, preventing a newly created Decision from
  expiring immediately at a completed-session boundary.
- Test source rows exist only in test profiles/Testcontainers. Provider HTTP, live-account,
  live-order, and broker-publish calls remain zero.

## Implementation and drift status

- The canonical catalog is `contracts/catalogs/s2-3-decision-contract.v1.json`, with SHA-256
  `d035607af50a0f7cb9cd7170e9a6a188e6af32d5bbbdb76e5e4f7b3edc68cd18`. The review
  `58e55ebda0154a079cff3d5c2527da66743cf3fdeeaf063b86b23b581371fab3` and provisional
  `58b658a1482b378d5a7c8c394381a14b6ad6e41c222d2f84e4edec65c1ab1e6f` digests are superseded
  evidence.
- Tracked `contracts/openapi/openapi.json` carries the same digest as
  `x-s2-3-contract-sha256` and permits exactly three Decision paths and five `S23*` components.
- V9 appends Decision, trace, artifact, audit, outbox, and idempotency state in one transaction.
  The application role is denied history rewrites, unrelated tables, Flyway history, and schema DDL.
- Stored quote, instrument catalog, KIS_MOCK portfolio, deterministic risk/order-count, and
  corporation-registry
  offline producers are S2.3 prerequisites owned by their respective modules. Missing machinery is
  a hard structural blocker; a missing row after readiness is a persisted 200 HOLD. S2.3 never
  hides either state with fake production rows or a provider fallback.
